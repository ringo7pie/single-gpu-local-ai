"""acraft の CLI 本体。

`run-audiocraft`（= venv の python）から `python -m acraft.cli <サブコマンド>` として呼ばれる。
このモジュール自体は標準ライブラリのみに依存する。torch を使う確認（doctor）は venv の python を
subprocess で呼んで行い、生成（generate）だけが acraft.engine を関数の中で import する。
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
)

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
for module_name in ("xformers", "soundfile", "spacy", "transformers", "gradio"):
    try:
        module = __import__(module_name)
        result[module_name + "_version"] = getattr(module, "__version__", "?")
    except Exception as exc:
        result[module_name + "_error"] = f"{{type(exc).__name__}}: {{exc}}"
try:
    import audiocraft
    from audiocraft.models import AudioGen, MusicGen  # noqa: F401
    result["audiocraft_version"] = audiocraft.__version__
except Exception as exc:
    result["audiocraft_error"] = f"{{type(exc).__name__}}: {{exc}}"
print(json.dumps(result))
"""

TORCH_EXPECTED_VERSION_PREFIX = "2.1.0"
TORCH_EXPECTED_CUDA_PREFIX = "12.1"
XFORMERS_EXPECTED_VERSION_PREFIX = "0.0.22"


# --------------------------------------------------------------------------
# 純粋関数（試験の中心。ファイルシステムにもネットワークにも触れない）
# --------------------------------------------------------------------------


def build_runtime_env(base_env: Mapping[str, str]) -> dict[str, str]:
    """画面（acraft.ui）や確認用の python に渡す環境変数を組み立てる。base_env は書き換えない。"""

    env = dict(base_env)
    env.update(config.runtime_env_overrides())
    return env


def build_serve_command(python_bin: str, host: str, port: int, device: str | None) -> list[str]:
    """画面を起動するコマンド列。"""

    command = [python_bin, "-m", "acraft.ui", "--host", host, "--port", str(port)]
    if device:
        command += ["--device", device]
    return command


def classify_port_status(port: int, in_use: bool, service_active: bool) -> tuple[bool, str]:
    """ポートの状態を doctor 向けに解釈する。使用中でも、それが自分（audiocraft）なら異常ではない。"""

    if not in_use:
        return True, f"ポート {port} は空いています（起動できます）"
    if service_active:
        return True, f"ポート {port} は AudioCraft が使用中です（稼働しています）"
    return False, f"ポート {port} を別のプロセスが使用中です"


def classify_vram_status(used_mib: int | None, threshold_mib: int, service_active: bool) -> tuple[bool, str]:
    """VRAM の状態を doctor 向けに解釈する。止まっているのに埋まっているなら、Qwen などが載っている。"""

    if used_mib is None:
        return False, "nvidia-smi の実行に失敗しました"
    if service_active:
        return True, f"使用中 {used_mib} MiB（AudioCraft が稼働中）"
    if used_mib < threshold_mib:
        return True, f"使用中 {used_mib} MiB（空いています）"
    return (
        False,
        f"使用中 {used_mib} MiB（AudioCraft は停止中。ほかの GPU サービスや Qwen が載っています。"
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

    xformers_error = probe.get("xformers_error")
    xformers_version = probe.get("xformers_version")
    if xformers_error:
        rows.append(("xformers の import", False, str(xformers_error)))
    else:
        rows.append(
            (
                f"xformers が {XFORMERS_EXPECTED_VERSION_PREFIX} 系",
                isinstance(xformers_version, str) and xformers_version.startswith(XFORMERS_EXPECTED_VERSION_PREFIX),
                str(xformers_version),
            )
        )

    for module_name in ("soundfile", "spacy", "transformers", "gradio"):
        error = probe.get(module_name + "_error")
        version = probe.get(module_name + "_version")
        rows.append((f"{module_name} の import", not error, str(error) if error else str(version)))

    audiocraft_error = probe.get("audiocraft_error")
    if audiocraft_error:
        rows.append(("audiocraft の import（MusicGen / AudioGen）", False, str(audiocraft_error)))
    else:
        rows.append(("audiocraft の import（MusicGen / AudioGen）", True, f"audiocraft {probe.get('audiocraft_version')}"))
    return rows


def hub_cache_dir_name(repo_id: str) -> str:
    """Hugging Face のキャッシュでのリポジトリのディレクトリ名。例: facebook/musicgen-medium -> models--facebook--musicgen-medium"""

    return "models--" + repo_id.replace("/", "--")


def find_weight_files(cache_dir: Path, repo_id: str, filenames: tuple[str, ...] = config.WEIGHT_FILES) -> list[str]:
    """cache_dir（hub キャッシュの根）に、repo_id の重みのうち実在するファイル名を返す。"""

    snapshots = cache_dir / hub_cache_dir_name(repo_id) / "snapshots"
    if not snapshots.is_dir():
        return []
    found: list[str] = []
    for filename in filenames:
        if any(candidate.is_file() for candidate in snapshots.glob(f"*/{filename}")):
            found.append(filename)
    return found


def text_encoder_present(hf_home: Path, model_name: str = config.TEXT_ENCODER_MODEL) -> bool:
    """T5（文章の符号化器）の重みとトークナイザが transformers のキャッシュにあるか。

    t5-base は Hugging Face 側で google-t5/t5-base へ移っているため、どちらの名前のディレクトリも見る。
    """

    hub = hf_home / "hub"
    if not hub.is_dir():
        return False
    for repo_dir in hub.glob(f"models--*{model_name}"):
        snapshots = repo_dir / "snapshots"
        has_weights = any(snapshots.glob("*/model.safetensors")) or any(snapshots.glob("*/pytorch_model.bin"))
        has_tokenizer = any(snapshots.glob("*/spiece.model"))
        if has_weights and has_tokenizer:
            return True
    return False


def summarize_weights(found: list[str], required: tuple[str, ...] = config.WEIGHT_FILES) -> tuple[bool, str]:
    missing = [name for name in required if name not in found]
    if missing:
        return False, f"足りないファイル: {', '.join(missing)}"
    return True, f"{', '.join(required)} あり"


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
            capture_output=True,
            text=True,
            timeout=timeout,
            env=dict(env),
            cwd=str(config.TOOL_DIR),
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
            ["git", "-C", str(config.VENDOR_DIR), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
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
    """audiocraft.service（systemd user ユニット）が動いているか。"""

    try:
        completed = subprocess.run(
            ["systemctl", "--user", "is-active", config.SERVICE_UNIT],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return completed.stdout.strip() == "active"


def _read_env_file_value(var_name: str) -> str | None:
    """~/.config/audiocraft/audiocraft.env から変数の値を読む（doctor の表示専用）。"""

    if not config.ENV_FILE.is_file():
        return None
    for line in config.ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip() == var_name:
            return value.strip()
    return None


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

    if not config.VENDOR_DIR.is_dir():
        print(f"上流が見つかりません: {config.VENDOR_DIR}（setup-server.sh を実行してください）", file=sys.stderr)
        return 1
    if not config.VENV_PYTHON.is_file():
        print(f"venv の python が見つかりません: {config.VENV_PYTHON}（setup-server.sh を実行してください）", file=sys.stderr)
        return 1

    music_id, sound_id = config.model_ids_from_env(os.environ)
    print(f"モデル: 作曲={music_id} / 効果音={sound_id}")

    command = build_serve_command(str(config.VENV_PYTHON), args.host, args.port, args.device)
    env = build_runtime_env(os.environ)
    for directory in (config.OUTPUTS_DIR, config.AUDIOCRAFT_CACHE_DIR, config.HF_HOME_DIR, config.TORCH_HOME_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    print(f"起動します（cwd={config.TOOL_DIR}）: {' '.join(command)}")
    os.chdir(config.TOOL_DIR)
    # execve はプロセスを置き換えるので、ここまでの print がバッファに残っていると捨てられる
    # （systemd 配下では stdout が端末でない）。journalctl に起動条件が残るよう、必ず流しておく。
    sys.stdout.flush()
    sys.stderr.flush()
    os.execve(command[0], command, env)
    return 1  # execve が成功すればここには戻らない。


def cmd_doctor(args: argparse.Namespace) -> int:
    results: list[tuple[str, bool, str]] = []

    results.append(("venv の python が存在", config.VENV_PYTHON.is_file(), str(config.VENV_PYTHON)))

    probe_env = build_runtime_env(os.environ)
    if args.skip_gpu:
        # GPU を一切触らない（CUDA の初期化も、ほかのプロセスの VRAM への影響も無い）。
        probe_env["CUDA_VISIBLE_DEVICES"] = ""
    probe = _run_venv_python(_ENV_PROBE.format(skip_gpu=bool(args.skip_gpu)), timeout=300, env=probe_env)
    results.extend(evaluate_probe(probe, bool(args.skip_gpu)))

    results.append(("上流が指定コミットで checkout 済み", *_check_upstream_commit()))

    music_id, sound_id = config.model_ids_from_env(os.environ)
    for label, repo_id in (("作曲", music_id), ("効果音", sound_id)):
        ok, detail = summarize_weights(find_weight_files(config.AUDIOCRAFT_CACHE_DIR, repo_id))
        results.append((f"{label}モデルの重み（{repo_id}）", ok, detail))
    results.append(
        (
            f"文章の符号化器（{config.TEXT_ENCODER_MODEL}）",
            text_encoder_present(config.HF_HOME_DIR),
            str(config.HF_HOME_DIR / "hub"),
        )
    )

    service_active = _service_is_active()
    if args.skip_gpu:
        results.append(("VRAM", True, "省略しました（--skip-gpu）"))
    else:
        results.append(("VRAM", *classify_vram_status(ollama.gpu_used_mib(), config.VRAM_FREE_THRESHOLD_MIB, service_active)))
    results.append(("ポート", *classify_port_status(config.PORT, _port_in_use(config.PORT), service_active)))
    results.append(("systemd ユニットの配置", config.SERVICE_FILE.is_file(), str(config.SERVICE_FILE)))

    # 情報表示。env ファイルが無い・モデルを差し替えていないのはどちらも正常なので常に OK にする。
    env_note = (
        f"{config.ENV_FILE} が無い（既定のモデル）"
        if not config.ENV_FILE.is_file()
        else f"{config.ENV_FILE}（{config.MUSIC_MODEL_ENV_VAR}={_read_env_file_value(config.MUSIC_MODEL_ENV_VAR)!r}, "
        f"{config.SOUND_MODEL_ENV_VAR}={_read_env_file_value(config.SOUND_MODEL_ENV_VAR)!r}）"
    )
    results.append(("モデル設定（env ファイル）", True, env_note))

    overall_ok = True
    for label, ok, detail in results:
        print(f"{'OK' if ok else 'NG'}  {label}: {detail}")
        overall_ok = overall_ok and ok
    return 0 if overall_ok else 1


def cmd_fetch_models(args: argparse.Namespace) -> int:
    if not config.VENV_PYTHON.is_file():
        print(f"venv の python が見つかりません: {config.VENV_PYTHON}（setup-server.sh を実行してください）", file=sys.stderr)
        return 1

    music_id, sound_id = config.model_ids_from_env(os.environ)
    overall_rc = 0
    env = build_runtime_env(os.environ)
    for directory in (config.AUDIOCRAFT_CACHE_DIR, config.HF_HOME_DIR, config.TORCH_HOME_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    for repo_id in dict.fromkeys((music_id, sound_id)):
        found = find_weight_files(config.AUDIOCRAFT_CACHE_DIR, repo_id)
        missing = [name for name in config.WEIGHT_FILES if name not in found]
        if not missing:
            print(f"スキップ（既にあります）: {repo_id}")
            continue
        for filename in missing:
            print(f"取得します: {repo_id}/{filename}")
            script = (
                "import huggingface_hub\n"
                f"path = huggingface_hub.hf_hub_download(repo_id={repo_id!r}, filename={filename!r}, "
                f"cache_dir={str(config.AUDIOCRAFT_CACHE_DIR)!r})\n"
                "print(path)\n"
            )
            completed = subprocess.run([str(config.VENV_PYTHON), "-c", script], env=env)
            if completed.returncode != 0:
                print(f"失敗しました: {repo_id}/{filename}", file=sys.stderr)
                overall_rc = 1

    if text_encoder_present(config.HF_HOME_DIR):
        print(f"スキップ（既にあります）: {config.TEXT_ENCODER_MODEL}")
    else:
        print(f"取得します: {config.TEXT_ENCODER_MODEL}（文章の符号化器）")
        script = (
            "from transformers import T5EncoderModel, T5Tokenizer\n"
            f"T5Tokenizer.from_pretrained({config.TEXT_ENCODER_MODEL!r})\n"
            f"T5EncoderModel.from_pretrained({config.TEXT_ENCODER_MODEL!r})\n"
            "print('ok')\n"
        )
        completed = subprocess.run([str(config.VENV_PYTHON), "-c", script], env=env)
        if completed.returncode != 0:
            print(f"失敗しました: {config.TEXT_ENCODER_MODEL}", file=sys.stderr)
            overall_rc = 1
    return overall_rc


def cmd_generate(args: argparse.Namespace) -> int:
    try:
        request = params.normalize_params(
            args.kind, args.prompt, args.duration, args.seed, args.top_k, args.top_p, args.temperature, args.cfg_coef
        )
    except params.ParamError as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 2

    os.environ.update(config.runtime_env_overrides())
    if args.device == "cpu":
        # torch を import する前に GPU を見えなくする（GPU を一切使わない確認用）。
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
    for directory in (config.AUDIOCRAFT_CACHE_DIR, config.HF_HOME_DIR, config.TORCH_HOME_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    from . import engine  # torch / audiocraft を読み込むため、ここまで遅らせる

    music_id, sound_id = config.model_ids_from_env(os.environ)
    device = None if args.device == "auto" else args.device
    generator = engine.Engine(music_id, sound_id, device=device)
    generator.load(kinds=(request.kind,))
    result = generator.generate(request, out_path=Path(args.out) if args.out else None)
    print(json.dumps(result.metadata, ensure_ascii=False, indent=2))
    print(f"wav: {result.wav_path}")
    return 0


# --------------------------------------------------------------------------
# 引数
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acraft", description="AudioCraft（MusicGen / AudioGen）をこの A30 サーバーで動かすための CLI"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="画面（Gradio）を起動する。モデルは起動時に 2 つとも読み込む")
    serve_parser.add_argument("--port", type=int, default=config.PORT)
    serve_parser.add_argument("--host", default=config.HOST)
    serve_parser.add_argument("--device", default=None, choices=("cuda", "cpu"), help="未指定なら GPU があれば cuda")
    serve_parser.add_argument("--skip-vram-check", action="store_true", help="VRAM の空き確認を省略して続行する")
    serve_parser.set_defaults(func=cmd_serve)

    doctor_parser = subparsers.add_parser("doctor", help="動作環境を点検する")
    doctor_parser.add_argument("--skip-gpu", action="store_true", help="GPU を一切触らずに点検する（CUDA の初期化・VRAM の確認を省く）")
    doctor_parser.set_defaults(func=cmd_doctor)

    fetch_parser = subparsers.add_parser("fetch-models", help="重みと文章の符号化器を取得する（既にあればスキップ。約 8.2 GB）")
    fetch_parser.set_defaults(func=cmd_fetch_models)

    generate_parser = subparsers.add_parser("generate", help="画面を通さずに 1 件生成する（実機試験・自動化用）")
    generate_parser.add_argument("--kind", required=True, choices=params.KINDS)
    generate_parser.add_argument("--prompt", required=True)
    generate_parser.add_argument("--duration", type=float, default=None, help="秒。未指定なら種類ごとの既定")
    generate_parser.add_argument("--seed", type=int, default=params.SEED_RANDOM)
    generate_parser.add_argument("--top-k", type=int, default=params.DEFAULT_TOP_K, dest="top_k")
    generate_parser.add_argument("--top-p", type=float, default=params.DEFAULT_TOP_P, dest="top_p")
    generate_parser.add_argument("--temperature", type=float, default=params.DEFAULT_TEMPERATURE)
    generate_parser.add_argument("--cfg-coef", type=float, default=params.DEFAULT_CFG_COEF, dest="cfg_coef")
    generate_parser.add_argument("--device", default="auto", choices=("auto", "cuda", "cpu"), help="cpu なら GPU を一切使わない")
    generate_parser.add_argument("--out", default=None, help="wav の出力先。未指定なら outputs/ に日時入りの名前で置く")
    generate_parser.set_defaults(func=cmd_generate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "generate" and args.duration is None:
        args.duration = params.KIND_SPECS[args.kind].default_duration
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
