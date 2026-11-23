"""saudio の CLI 本体。

`run-stable-audio`（= venv の python）から `python -m saudio.cli <サブコマンド>` として呼ばれる。
このモジュール自体は標準ライブラリのみに依存する。torch を使う確認（doctor）は venv の python を
subprocess で呼んで行い、生成（generate）だけが saudio.engine を関数の中で import する。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from . import config, ollama, params

# 起動前に止める必要がある、GPU を使う他のサービス（tools/portal/portal.py の GPU_EXCLUSIVE_KEYS と同じ顔ぶれ）。
# systemd ユニットの Conflicts=/After= と必ず一致させる（試験で雛形と突き合わせる）。
GPU_CONFLICT_UNITS = (
    "hunyuan3d",
    "pixal3d",
    "comfyui",
    "strata",
    "strata-coder",
    "strata-official",
    "strata-official-iq3s",
    "strata-swift",
    "audiocraft",
    "qwen3-tts",
)

# serve が「導入が済んでいない（上流・venv・重みが無い）」で止まるときの終了コード。unit の RestartPreventExitStatus= と一致させる。
# 再起動しても直らないので、systemd に再起動させない（2026-11-23、重みの無いまま起動され 5 秒おきに 462 回再起動した）。
SETUP_INCOMPLETE_EXIT = 78

TORCH_EXPECTED_VERSION_PREFIX = "2.7.1"
TORCH_EXPECTED_CUDA_PREFIX = "12.6"
FLASH_ATTN_EXPECTED_VERSION_PREFIX = "2.8.3"

# venv の python で実行する確認用スクリプト。{skip_gpu} が真のときは CUDA の初期化（デバイス名の取得）をしない。
_ENV_PROBE = """
import json

SKIP_GPU = {skip_gpu}
result = {{}}
try:
    import torch
except Exception as exc:
    result["torch_error"] = f"{{type(exc).__name__}}: {{exc}}"
else:
    result["torch_version"] = torch.__version__
    result["cuda_version"] = torch.version.cuda
    if not SKIP_GPU:
        result["cuda_available"] = bool(torch.cuda.is_available())
        if result["cuda_available"]:
            try:
                result["device_name"] = torch.cuda.get_device_name(0)
            except Exception as exc:
                result["device_name_error"] = str(exc)
for module_name in ("flash_attn", "soundfile", "transformers", "gradio", "fastapi"):
    try:
        module = __import__(module_name)
        result[module_name + "_version"] = getattr(module, "__version__", "?")
    except Exception as exc:
        result[module_name + "_error"] = f"{{type(exc).__name__}}: {{exc}}"
try:
    from stable_audio_3 import StableAudioModel  # noqa: F401
    result["stable_audio_3_ok"] = True
except Exception as exc:
    result["stable_audio_3_error"] = f"{{type(exc).__name__}}: {{exc}}"
print(json.dumps(result))
"""


# --------------------------------------------------------------------------
# 純粋関数（試験の中心。ファイルシステムにもネットワークにも触れない）
# --------------------------------------------------------------------------


def build_runtime_env(base_env: Mapping[str, str], offline: bool = True) -> dict[str, str]:
    """画面（saudio.ui）や確認用の python に渡す環境変数を組み立てる。base_env は書き換えない。"""

    env = dict(base_env)
    env.update(config.runtime_env_overrides(offline=offline))
    return env


def build_serve_command(python_bin: str, host: str, port: int, device: str | None) -> list[str]:
    """画面を起動するコマンド列。"""

    command = [python_bin, "-m", "saudio.ui", "--host", host, "--port", str(port)]
    if device:
        command += ["--device", device]
    return command


def classify_port_status(port: int, in_use: bool, service_active: bool) -> tuple[bool, str]:
    """ポートの状態を doctor 向けに解釈する。使用中でも、それが自分（stable-audio）なら異常ではない。"""

    if not in_use:
        return True, f"ポート {port} は空いています（起動できます）"
    if service_active:
        return True, f"ポート {port} は Stable Audio が使用中です（稼働しています）"
    return False, f"ポート {port} を別のプロセスが使用中です"


def classify_vram_status(used_mib: int | None, threshold_mib: int, service_active: bool) -> tuple[bool, str]:
    """VRAM の状態を doctor 向けに解釈する。止まっているのに埋まっているなら、Qwen などが載っている。"""

    if used_mib is None:
        return False, "nvidia-smi の実行に失敗しました"
    if service_active:
        return True, f"使用中 {used_mib} MiB（Stable Audio が稼働中）"
    if used_mib < threshold_mib:
        return True, f"使用中 {used_mib} MiB（空いています）"
    return (
        False,
        f"使用中 {used_mib} MiB（Stable Audio は停止中。ほかの GPU サービスや Qwen が載っています。"
        "入口ページから起動すると自動で降ろします）",
    )


def evaluate_probe(probe: Mapping[str, Any], skip_gpu: bool) -> list[tuple[str, bool, str]]:
    """venv の python で取った確認結果（_ENV_PROBE の出力）を、doctor の行にする。"""

    rows: list[tuple[str, bool, str]] = []
    torch_error = probe.get("torch_error")
    torch_version = probe.get("torch_version")
    cuda_version = probe.get("cuda_version")
    if torch_error:
        rows.append(("torch の import", False, str(torch_error)))
    else:
        rows.append(("torch の import", True, f"torch {torch_version}"))
        rows.append(
            (
                f"torch が {TORCH_EXPECTED_VERSION_PREFIX} 系",
                isinstance(torch_version, str) and torch_version.startswith(TORCH_EXPECTED_VERSION_PREFIX),
                str(torch_version),
            )
        )
        rows.append(
            (
                f"torch.version.cuda が {TORCH_EXPECTED_CUDA_PREFIX} 系",
                isinstance(cuda_version, str) and cuda_version.startswith(TORCH_EXPECTED_CUDA_PREFIX),
                str(cuda_version) if cuda_version else "(取得できず)",
            )
        )
        if skip_gpu:
            rows.append(("GPU の確認", True, "省略しました（--skip-gpu）"))
        else:
            cuda_available = bool(probe.get("cuda_available"))
            rows.append(("torch.cuda.is_available()", cuda_available, str(cuda_available)))
            device_name = probe.get("device_name") or ""
            rows.append(("GPU デバイス名に A30 を含む", cuda_available and "A30" in device_name, device_name or "(取得できず)"))

    flash_error = probe.get("flash_attn_error")
    flash_version = probe.get("flash_attn_version")
    if flash_error:
        rows.append(("flash_attn の import（Medium に必須）", False, str(flash_error)))
    else:
        rows.append(
            (
                f"flash_attn が {FLASH_ATTN_EXPECTED_VERSION_PREFIX} 系",
                isinstance(flash_version, str) and flash_version.startswith(FLASH_ATTN_EXPECTED_VERSION_PREFIX),
                str(flash_version),
            )
        )
    for module_name in ("soundfile", "transformers", "gradio", "fastapi"):
        error = probe.get(module_name + "_error")
        version = probe.get(module_name + "_version")
        rows.append((f"{module_name} の import", not error, str(error) if error else str(version)))

    sa3_error = probe.get("stable_audio_3_error")
    rows.append(("stable_audio_3 の import", not sa3_error and bool(probe.get("stable_audio_3_ok")), str(sa3_error or "OK")))
    return rows


def hub_repo_dir(cache_dir: Path, repo_id: str) -> Path:
    """Hugging Face のキャッシュでのリポジトリのディレクトリ。例: models--stabilityai--stable-audio-3-medium"""

    return cache_dir / ("models--" + repo_id.replace("/", "--"))


def required_model_files() -> tuple[str, ...]:
    """推論に要るファイル（リポジトリの中のパス）。文章の符号化器は t5gemma-b-b-ul2/ の下。"""

    encoder = config.TEXT_ENCODER_SUBFOLDER
    return config.MODEL_FILES + tuple(
        f"{encoder}/{name}"
        for name in ("config.json", "model.safetensors", "tokenizer.json", "tokenizer.model", "tokenizer_config.json", "special_tokens_map.json")
    )


def check_model_cache(cache_dir: Path, repo_id: str, revision: str) -> tuple[bool, str]:
    """重みが揃い、refs/main が固定のリビジョンを指しているか。(合否, 説明)。"""

    repo_dir = hub_repo_dir(cache_dir, repo_id)
    snapshot = repo_dir / "snapshots" / revision
    if not snapshot.is_dir():
        return False, f"リビジョン {revision[:12]} のスナップショットがありません（run-stable-audio fetch-models）"
    missing = [name for name in required_model_files() if not (snapshot / name).is_file()]
    if missing:
        return False, f"足りないファイル: {', '.join(missing)}"
    ref_file = repo_dir / "refs" / "main"
    ref = ref_file.read_text(encoding="utf-8").strip() if ref_file.is_file() else ""
    if ref != revision:
        return False, f"refs/main が {ref[:12] or '(無し)'} を指しています（期待値 {revision[:12]}。fetch-models で直ります）"
    return True, f"{len(required_model_files())} ファイルあり（{revision[:12]}）"


def read_token(environ: Mapping[str, str], token_file: Path) -> str | None:
    """Hugging Face のトークンを、環境変数 HF_TOKEN かトークンのファイルから読む（無ければ None）。値は表示しない。"""

    value = (environ.get("HF_TOKEN") or "").strip()
    if value:
        return value
    try:
        text = token_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


TOKEN_HELP = f"""\
Stable Audio 3.0 Medium の重み（{config.MODEL_REPO}）は、Hugging Face で規約に同意したアカウントだけが取れます。
  1. https://huggingface.co/{config.MODEL_REPO} を開き、ログインして規約に同意する（自動で承認されます）
  2. https://huggingface.co/settings/tokens で Read 権限のトークンを作る
  3. このサーバーでトークンを置く（画面に出さないよう read -s で入力）:
       install -d -m 700 {config.CONFIG_DIR}
       ( umask 077; read -rsp 'HF token: ' token; echo; printf '%s\\n' "$token" > {config.HF_TOKEN_FILE} )
     （環境変数 HF_TOKEN でも渡せます）
  4. もう一度 run-stable-audio fetch-models を実行する"""


# --------------------------------------------------------------------------
# 副作用のある補助
# --------------------------------------------------------------------------


def _run_venv_python(script: str, timeout: float, env: Mapping[str, str]) -> dict[str, Any]:
    """venv の python で script を -c 実行し、最後の行を JSON として読む。"""

    if not config.VENV_PYTHON.is_file():
        return {"torch_error": f"venv の python がありません: {config.VENV_PYTHON}"}
    try:
        completed = subprocess.run(
            [str(config.VENV_PYTHON), "-c", script],
            capture_output=True, text=True, timeout=timeout, env=dict(env), cwd=str(config.TOOL_DIR),
        )
    except (subprocess.SubprocessError, OSError) as error:
        return {"torch_error": f"venv の python の起動に失敗しました: {error}"}
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        return {"torch_error": completed.stderr.strip()[-500:] or "標準出力が空でした"}
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return {"torch_error": completed.stderr.strip()[-500:] or lines[-1]}


def _check_upstream_commit() -> tuple[bool, str]:
    if not config.VENDOR_DIR.is_dir():
        return False, f"上流が見つかりません: {config.VENDOR_DIR}"
    try:
        completed = subprocess.run(
            ["git", "-C", str(config.VENDOR_DIR), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10, check=True,
        )
    except (subprocess.SubprocessError, OSError) as error:
        return False, f"git rev-parse に失敗しました: {error}"
    head = completed.stdout.strip()
    if head == config.UPSTREAM_COMMIT:
        return True, f"HEAD={head}"
    return False, f"HEAD={head}（期待値 {config.UPSTREAM_COMMIT}）"


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _service_is_active() -> bool:
    try:
        completed = subprocess.run(
            ["systemctl", "--user", "is-active", config.SERVICE_UNIT], capture_output=True, text=True, timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return completed.stdout.strip() == "active"


# --------------------------------------------------------------------------
# サブコマンド
# --------------------------------------------------------------------------


def cmd_serve(args: argparse.Namespace) -> int:
    print("Ollama の常駐モデル:")
    before = ollama.loaded_models()
    if before:
        for model in before:
            print(f"  - {model['name']} (size_vram={model['size_vram']})")
    else:
        print("  (なし)")

    if args.skip_vram_check:
        print("警告: --skip-vram-check により VRAM の空き確認を省略しました。ほかの GPU サービスと同時に載せると OOM の恐れがあります。", file=sys.stderr)
    elif not ollama.wait_until_free(config.VRAM_FREE_THRESHOLD_MIB, config.VRAM_WAIT_SECONDS):
        used = ollama.gpu_used_mib()
        print("VRAM を確保できませんでした。中止します。", file=sys.stderr)
        print(f"  使用中 VRAM: {used} MiB（閾値 {config.VRAM_FREE_THRESHOLD_MIB} MiB 未満が必要）", file=sys.stderr)
        print("  入口ページから起動すると、Ollama のモデルと ほかの GPU サービスを先に降ろします。", file=sys.stderr)
        return 1

    for path, label in ((config.VENDOR_DIR, "上流"), (config.VENV_PYTHON, "venv の python")):
        if not path.exists():
            print(f"{label}が見つかりません: {path}（setup-server.sh を実行してください）", file=sys.stderr)
            return SETUP_INCOMPLETE_EXIT
    ok, detail = check_model_cache(config.HF_HUB_CACHE_DIR, config.MODEL_REPO, config.MODEL_REVISION)
    if not ok:
        print(f"重みが揃っていません: {detail}（run-stable-audio fetch-models。再起動はしません）", file=sys.stderr)
        return SETUP_INCOMPLETE_EXIT

    command = build_serve_command(str(config.VENV_PYTHON), args.host, args.port, args.device)
    env = build_runtime_env(os.environ)
    config.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"起動します（cwd={config.TOOL_DIR}）: {' '.join(command)}")
    os.chdir(config.TOOL_DIR)
    sys.stdout.flush()
    sys.stderr.flush()
    os.execve(command[0], command, env)
    return 1  # execve が成功すればここには戻らない。


def cmd_doctor(args: argparse.Namespace) -> int:
    results: list[tuple[str, bool, str]] = []
    results.append(("venv の python が存在", config.VENV_PYTHON.is_file(), str(config.VENV_PYTHON)))

    probe_env = build_runtime_env(os.environ)
    if args.skip_gpu:
        probe_env["CUDA_VISIBLE_DEVICES"] = ""
    probe = _run_venv_python(_ENV_PROBE.format(skip_gpu=bool(args.skip_gpu)), timeout=300, env=probe_env)
    results.extend(evaluate_probe(probe, bool(args.skip_gpu)))
    results.append(("上流が指定コミットで checkout 済み", *_check_upstream_commit()))
    results.append((f"重み（{config.MODEL_REPO}）", *check_model_cache(config.HF_HUB_CACHE_DIR, config.MODEL_REPO, config.MODEL_REVISION)))

    service_active = _service_is_active()
    if args.skip_gpu:
        results.append(("VRAM", True, "省略しました（--skip-gpu）"))
    else:
        results.append(("VRAM", *classify_vram_status(ollama.gpu_used_mib(), config.VRAM_FREE_THRESHOLD_MIB, service_active)))
    results.append(("ポート", *classify_port_status(config.PORT, _port_in_use(config.PORT), service_active)))
    results.append(("systemd ユニットの配置", config.SERVICE_FILE.is_file(), str(config.SERVICE_FILE)))

    overall_ok = True
    for label, ok, detail in results:
        print(f"{'OK' if ok else 'NG'}  {label}: {detail}")
        overall_ok = overall_ok and ok
    return 0 if overall_ok else 1


def cmd_fetch_models(args: argparse.Namespace) -> int:
    if not config.VENV_PYTHON.is_file():
        print(f"venv の python が見つかりません: {config.VENV_PYTHON}（setup-server.sh を実行してください）", file=sys.stderr)
        return 1
    ok, detail = check_model_cache(config.HF_HUB_CACHE_DIR, config.MODEL_REPO, config.MODEL_REVISION)
    if ok:
        print(f"スキップ（既にあります）: {config.MODEL_REPO}（{detail}）")
        return 0

    token = read_token(os.environ, config.HF_TOKEN_FILE)
    if token is None:
        print("Hugging Face のトークンがありません。", file=sys.stderr)
        print(TOKEN_HELP, file=sys.stderr)
        return 3

    env = build_runtime_env(os.environ, offline=False)
    env["HF_TOKEN"] = token  # 子プロセスにだけ渡す。表示しない。
    config.HF_HUB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    patterns = list(required_model_files())
    print(f"取得します: {config.MODEL_REPO}@{config.MODEL_REVISION[:12]}（約 10.5 GB。{len(patterns)} ファイル）")
    # 上流のコードは revision を付けずに main を読むので、取得後に refs/main を固定のリビジョンへ向ける
    # （huggingface_hub がブランチ名で取ったときに書くのと同じ形）。
    script = (
        "import pathlib, huggingface_hub\n"
        f"path = huggingface_hub.snapshot_download(repo_id={config.MODEL_REPO!r}, revision={config.MODEL_REVISION!r}, "
        f"allow_patterns={patterns!r}, cache_dir={str(config.HF_HUB_CACHE_DIR)!r})\n"
        f"ref = pathlib.Path({str(hub_repo_dir(config.HF_HUB_CACHE_DIR, config.MODEL_REPO) / 'refs' / 'main')!r})\n"
        "ref.parent.mkdir(parents=True, exist_ok=True)\n"
        f"ref.write_text({config.MODEL_REVISION!r})\n"
        "print(path)\n"
    )
    completed = subprocess.run([str(config.VENV_PYTHON), "-c", script], env=env)
    if completed.returncode != 0:
        print("取得に失敗しました。規約に同意したアカウントのトークンか確かめてください（403 / GatedRepoError なら未同意）。", file=sys.stderr)
        return 1
    ok, detail = check_model_cache(config.HF_HUB_CACHE_DIR, config.MODEL_REPO, config.MODEL_REVISION)
    print(f"{'OK' if ok else 'NG'}  重み: {detail}")
    return 0 if ok else 1


def cmd_generate(args: argparse.Namespace) -> int:
    try:
        source = params.source_from_path(args.source) if args.source else None
        request = params.normalize_params(
            args.prompt, args.duration, args.seed, args.steps, args.mode, source,
            args.init_noise_level, args.inpaint_start, args.inpaint_end,
        )
    except params.ParamError as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 2

    os.environ.update(config.runtime_env_overrides())
    if args.device == "cpu":
        os.environ["CUDA_VISIBLE_DEVICES"] = ""

    from . import engine  # torch / 上流を読み込むため、ここまで遅らせる

    generator = engine.Engine(device=None if args.device == "auto" else args.device)
    generator.load()
    try:
        result = generator.generate(request, out_path=Path(args.out) if args.out else None)
    except params.ParamError as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result.metadata, ensure_ascii=False, indent=2))
    print(f"wav: {result.wav_path}")
    return 0


# --------------------------------------------------------------------------
# 引数
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="saudio", description="Stable Audio 3.0 Medium をこの A30 サーバーで動かすための CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="画面（Gradio）と REST を起動する。モデルは起動時に読み込む")
    serve_parser.add_argument("--port", type=int, default=config.PORT)
    serve_parser.add_argument("--host", default=config.HOST)
    serve_parser.add_argument("--device", default=None, choices=("cuda", "cpu"), help="未指定なら GPU があれば cuda")
    serve_parser.add_argument("--skip-vram-check", action="store_true", help="VRAM の空き確認を省略して続行する")
    serve_parser.set_defaults(func=cmd_serve)

    doctor_parser = subparsers.add_parser("doctor", help="動作環境を点検する")
    doctor_parser.add_argument("--skip-gpu", action="store_true", help="GPU を一切触らずに点検する（CUDA の初期化・VRAM の確認を省く）")
    doctor_parser.set_defaults(func=cmd_doctor)

    fetch_parser = subparsers.add_parser("fetch-models", help="重みと文章の符号化器を取得する（gated。HF のトークンが要る。約 10.5 GB）")
    fetch_parser.set_defaults(func=cmd_fetch_models)

    generate_parser = subparsers.add_parser("generate", help="画面を通さずに 1 件生成する（実機試験・自動化用。毎回モデルを読み込む）")
    generate_parser.add_argument("--prompt", required=True)
    generate_parser.add_argument("--duration", type=float, default=params.DEFAULT_DURATION)
    generate_parser.add_argument("--seed", type=int, default=params.SEED_RANDOM)
    generate_parser.add_argument("--steps", type=int, default=params.DEFAULT_STEPS)
    generate_parser.add_argument("--mode", default=params.MODE_TEXT, choices=params.MODES, help="audio・inpaint は --source が要る")
    generate_parser.add_argument("--source", default=None, help="元の音のファイル（wav・flac・ogg・mp3）")
    generate_parser.add_argument("--init-noise-level", type=float, default=params.DEFAULT_INIT_NOISE_LEVEL, dest="init_noise_level")
    generate_parser.add_argument("--inpaint-start", type=float, default=None, dest="inpaint_start")
    generate_parser.add_argument("--inpaint-end", type=float, default=None, dest="inpaint_end")
    generate_parser.add_argument("--device", default="auto", choices=("auto", "cuda", "cpu"))
    generate_parser.add_argument("--out", default=None, help="wav の出力先。未指定なら outputs/ に日時入りの名前で置く")
    generate_parser.set_defaults(func=cmd_generate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
