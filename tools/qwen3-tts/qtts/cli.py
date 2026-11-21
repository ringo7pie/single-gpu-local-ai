"""qtts の CLI 本体。

`run-qwen3-tts`（= venv の python）から `python -m qtts.cli <サブコマンド>` として呼ばれる。
このモジュール自体は標準ライブラリのみに依存する。torch を使う確認（doctor）は venv の python を
subprocess で呼んで行う。画面は上流の `qwen-tts-demo`（Gradio）をそのまま exec する。
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

from . import config, ollama

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
for module_name in ("transformers", "gradio", "soundfile"):
    try:
        module = __import__(module_name)
        result[module_name + "_version"] = getattr(module, "__version__", "?")
    except Exception as exc:
        result[module_name + "_error"] = f"{{type(exc).__name__}}: {{exc}}"
try:
    from importlib.metadata import version
    from qwen_tts import Qwen3TTSModel  # noqa: F401
    result["qwen_tts_version"] = version("qwen-tts")
except Exception as exc:
    result["qwen_tts_error"] = f"{{type(exc).__name__}}: {{exc}}"
print(json.dumps(result))
"""


# --------------------------------------------------------------------------
# 純粋関数（試験の中心。ファイルシステムにもネットワークにも触れない）
# --------------------------------------------------------------------------


def build_runtime_env(base_env: Mapping[str, str]) -> dict[str, str]:
    """画面や確認用の python に渡す環境変数を組み立てる。base_env は書き換えない。"""

    env = dict(base_env)
    env.update(config.runtime_env_overrides())
    return env


def build_serve_command(
    demo_bin: str, model_dir: str, host: str, port: int, dtype: str, flash_attn: bool, device: str | None
) -> list[str]:
    """上流の画面（qwen-tts-demo）を起動するコマンド列。flash-attn は上流の既定が有効なので、必ずどちらかを明示する。"""

    command = [demo_bin, model_dir, "--ip", host, "--port", str(port), "--dtype", dtype]
    # 上流の既定は同時 16 件。GPU 1 枚で並べて生成すると VRAM が読めなくなるので、1 件ずつ順番に処理させる。
    command += ["--concurrency", str(config.GRADIO_CONCURRENCY)]
    command.append("--flash-attn" if flash_attn else "--no-flash-attn")
    if device:
        command += ["--device", device]
    return command


def classify_port_status(port: int, in_use: bool, service_active: bool) -> tuple[bool, str]:
    """ポートの状態を doctor 向けに解釈する。使用中でも、それが自分（qwen3-tts）なら異常ではない。"""

    if not in_use:
        return True, f"ポート {port} は空いています（起動できます）"
    if service_active:
        return True, f"ポート {port} は Qwen3-TTS が使用中です（稼働しています）"
    return False, f"ポート {port} を別のプロセスが使用中です"


def classify_vram_status(used_mib: int | None, threshold_mib: int, service_active: bool) -> tuple[bool, str]:
    """VRAM の状態を doctor 向けに解釈する。止まっているのに埋まっているなら、Qwen などが載っている。"""

    if used_mib is None:
        return False, "nvidia-smi の実行に失敗しました"
    if service_active:
        return True, f"使用中 {used_mib} MiB（Qwen3-TTS が稼働中）"
    if used_mib < threshold_mib:
        return True, f"使用中 {used_mib} MiB（空いています）"
    return (
        False,
        f"使用中 {used_mib} MiB（Qwen3-TTS は停止中。ほかの GPU サービスや Ollama のモデルが載っています。"
        "入口ページから起動すると自動で降ろします）",
    )


def evaluate_probe(probe: Mapping[str, Any], skip_gpu: bool) -> list[tuple[str, bool, str]]:
    """venv の python で取った確認結果（_ENV_PROBE の出力）を、doctor の行にする。"""

    rows: list[tuple[str, bool, str]] = []
    torch_error = probe.get("torch_error")
    if torch_error:
        rows.append(("torch の import", False, str(torch_error)))
    else:
        rows.append(("torch の import", True, f"torch {probe.get('torch_version')}（CUDA {probe.get('cuda_version')}）"))
        if skip_gpu:
            rows.append(("CUDA", True, "省略しました（--skip-gpu）"))
        elif probe.get("cuda_available"):
            rows.append(("CUDA", True, str(probe.get("device_name", probe.get("device_name_error", "?")))))
        else:
            rows.append(("CUDA", False, "torch.cuda.is_available() が False"))
    for module_name in ("transformers", "gradio", "soundfile"):
        error = probe.get(module_name + "_error")
        if error:
            rows.append((f"{module_name} の import", False, str(error)))
        else:
            rows.append((f"{module_name} の import", True, str(probe.get(module_name + "_version", "?"))))
    qwen_error = probe.get("qwen_tts_error")
    qwen_version = probe.get("qwen_tts_version")
    if qwen_error:
        rows.append(("qwen-tts の import", False, str(qwen_error)))
    elif qwen_version != config.QWEN_TTS_VERSION:
        rows.append(("qwen-tts の import", False, f"{qwen_version}（期待値 {config.QWEN_TTS_VERSION}）"))
    else:
        rows.append(("qwen-tts の import", True, str(qwen_version)))
    return rows


def missing_model_files(model_dir: Path, required: tuple[str, ...] = config.REQUIRED_MODEL_FILES) -> list[str]:
    """重みのディレクトリに足りないファイル（相対パス）。空ならそろっている。"""

    return [name for name in required if not (model_dir / name).is_file()]


# --------------------------------------------------------------------------
# 外部に触る小道具
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


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _service_is_active() -> bool:
    """qwen3-tts.service（systemd user ユニット）が動いているか。"""

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

    if not config.DEMO_BIN.is_file():
        print(f"画面の起動コマンドが見つかりません: {config.DEMO_BIN}（setup-server.sh を実行してください）", file=sys.stderr)
        return 1
    missing = missing_model_files(config.MODEL_DIR)
    if missing:
        print(f"重みが足りません: {config.MODEL_DIR}（{', '.join(missing)}）。run-qwen3-tts fetch-models を実行してください", file=sys.stderr)
        return 1

    dtype, flash_attn = config.serve_options_from_env(os.environ)
    command = build_serve_command(
        str(config.DEMO_BIN), str(config.MODEL_DIR), args.host, args.port, dtype, flash_attn, args.device
    )
    env = build_runtime_env(os.environ)
    for directory in (config.OUTPUTS_DIR, config.HF_HOME_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    print(f"起動します（cwd={config.OUTPUTS_DIR}）: {' '.join(command)}")
    # Gradio の一時ファイルなどがリポジトリの直下に散らからないよう、outputs/ を作業ディレクトリにする。
    os.chdir(config.OUTPUTS_DIR)
    # execve はプロセスを置き換えるので、ここまでの print がバッファに残っていると捨てられる
    # （systemd 配下では stdout が端末でない）。journalctl に起動条件が残るよう、必ず流しておく。
    sys.stdout.flush()
    sys.stderr.flush()
    os.execve(command[0], command, env)
    return 1  # execve が成功すればここには戻らない。


def cmd_doctor(args: argparse.Namespace) -> int:
    results: list[tuple[str, bool, str]] = []
    results.append(("venv の python が存在", config.VENV_PYTHON.is_file(), str(config.VENV_PYTHON)))
    results.append(("画面の起動コマンド（qwen-tts-demo）", config.DEMO_BIN.is_file(), str(config.DEMO_BIN)))

    probe_env = build_runtime_env(os.environ)
    if args.skip_gpu:
        # GPU を一切触らない（CUDA の初期化も、ほかのプロセスの VRAM への影響も無い）。
        probe_env["CUDA_VISIBLE_DEVICES"] = ""
    probe = _run_venv_python(_ENV_PROBE.format(skip_gpu=bool(args.skip_gpu)), timeout=300, env=probe_env)
    results.extend(evaluate_probe(probe, bool(args.skip_gpu)))

    missing = missing_model_files(config.MODEL_DIR)
    results.append(
        (
            f"重み（{config.MODEL_REPO_ID}）",
            not missing,
            str(config.MODEL_DIR) if not missing else f"足りない: {', '.join(missing)}（run-qwen3-tts fetch-models）",
        )
    )

    service_active = _service_is_active()
    if args.skip_gpu:
        results.append(("VRAM", True, "省略しました（--skip-gpu）"))
    else:
        results.append(("VRAM", *classify_vram_status(ollama.gpu_used_mib(), config.VRAM_FREE_THRESHOLD_MIB, service_active)))
    results.append(("ポート", *classify_port_status(config.PORT, _port_in_use(config.PORT), service_active)))
    results.append(("systemd ユニットの配置", config.SERVICE_FILE.is_file(), str(config.SERVICE_FILE)))

    dtype, flash_attn = config.serve_options_from_env(os.environ)
    results.append(("起動の設定", True, f"dtype={dtype}, flash-attn={'使う' if flash_attn else '使わない（sdpa）'}"))

    overall_ok = True
    for label, ok, detail in results:
        print(f"{'OK' if ok else 'NG'}  {label}: {detail}")
        overall_ok = overall_ok and ok
    return 0 if overall_ok else 1


def cmd_fetch_models(args: argparse.Namespace) -> int:
    if not config.VENV_PYTHON.is_file():
        print(f"venv の python が見つかりません: {config.VENV_PYTHON}（setup-server.sh を実行してください）", file=sys.stderr)
        return 1
    if not missing_model_files(config.MODEL_DIR):
        print(f"スキップ（既にあります）: {config.MODEL_DIR}")
        return 0

    env = build_runtime_env(os.environ)
    config.HF_HOME_DIR.mkdir(parents=True, exist_ok=True)
    print(f"取得します: {config.MODEL_REPO_ID}@{config.MODEL_REVISION} → {config.MODEL_DIR}（約 4.3 GB）")
    script = (
        "import huggingface_hub\n"
        f"path = huggingface_hub.snapshot_download(repo_id={config.MODEL_REPO_ID!r}, "
        f"revision={config.MODEL_REVISION!r}, local_dir={str(config.MODEL_DIR)!r})\n"
        "print(path)\n"
    )
    completed = subprocess.run([str(config.VENV_PYTHON), "-c", script], env=env)
    if completed.returncode != 0:
        print(f"失敗しました: {config.MODEL_REPO_ID}", file=sys.stderr)
        return 1
    missing = missing_model_files(config.MODEL_DIR)
    if missing:
        print(f"取得後も足りないファイルがあります: {', '.join(missing)}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run-qwen3-tts", description="Qwen3-TTS VoiceDesign（tools/qwen3-tts）")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="上流の画面（qwen-tts-demo、Gradio）を起動する")
    serve_parser.add_argument("--host", default=config.HOST)
    serve_parser.add_argument("--port", type=int, default=config.PORT)
    serve_parser.add_argument("--device", default=None, help="例: cuda:0（省略時は上流の既定）")
    serve_parser.add_argument("--skip-vram-check", action="store_true", help="VRAM の空き確認を省く（非推奨）")
    serve_parser.set_defaults(func=cmd_serve)

    doctor_parser = subparsers.add_parser("doctor", help="動作環境を点検する")
    doctor_parser.add_argument("--skip-gpu", action="store_true", help="GPU を触らない（CUDA を初期化しない）")
    doctor_parser.set_defaults(func=cmd_doctor)

    fetch_parser = subparsers.add_parser("fetch-models", help="重みを取得する（既にあればスキップ。約 4.3 GB）")
    fetch_parser.set_defaults(func=cmd_fetch_models)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
