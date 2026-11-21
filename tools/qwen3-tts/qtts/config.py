"""パス・ポート・モデル ID を一元管理する。

このモジュールはファイルシステムに書き込まず、ネットワークにも触れない。
定数を並べるだけなので、素の python からでも torch なしで import できる。
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

# このファイルは tools/qwen3-tts/qtts/config.py なので、2 つ上が tools/qwen3-tts。
TOOL_DIR = Path(__file__).resolve().parents[1]

MODELS_DIR = TOOL_DIR / "models"
OUTPUTS_DIR = TOOL_DIR / "outputs"
VENV_DIR = TOOL_DIR / ".venv"
VENV_PYTHON = VENV_DIR / "bin" / "python"
# 上流（PyPI の qwen-tts）が入れる Gradio 画面の起動コマンド。
DEMO_BIN = VENV_DIR / "bin" / "qwen-tts-demo"

# 重みと実行時キャッシュは、すべて models/ の下に閉じ込める（~/.cache に混ぜない）。
HF_HOME_DIR = MODELS_DIR / "hf"

HOST = "0.0.0.0"
PORT = 7863
SERVICE_UNIT = "qwen3-tts"
SERVICE_FILE = Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_UNIT}.service"
ENV_FILE = Path.home() / ".config" / "qwen3-tts" / "qwen3-tts.env"

OLLAMA_URL = "http://127.0.0.1:11434"

# 上流のパッケージ。setup-server.sh / requirements.lock.txt の版と必ず一致させる（試験で確かめる）。
QWEN_TTS_VERSION = "0.1.1"

# 使うモデル。Hugging Face の sha（2026-11-21 時点の main）に固定して取る。
# model.safetensors と speech_tokenizer/（音声の符号化器）が同じリポジトリに入っている。
MODEL_REPO_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
MODEL_REVISION = "5ecdb67327fd37bb2e042aab12ff7391903235d3"
MODEL_DIR = MODELS_DIR / "Qwen3-TTS-12Hz-1.7B-VoiceDesign"
# 重みがそろっているかの判定に使うファイル（fetch-models 後に存在するはずのもの）。
REQUIRED_MODEL_FILES = (
    "config.json",
    "model.safetensors",
    "speech_tokenizer/config.json",
    "speech_tokenizer/model.safetensors",
)

# env ファイルで変えられる値。
DTYPE_ENV_VAR = "QWEN3TTS_DTYPE"
FLASH_ATTN_ENV_VAR = "QWEN3TTS_FLASH_ATTN"
DEFAULT_DTYPE = "bfloat16"  # A30（Ampere）は bf16 を持つ
ALLOWED_DTYPES = ("bfloat16", "float16", "float32")

# 画面の同時生成数（qwen-tts-demo の --concurrency）。上流の既定 16 ではなく 1 件ずつ。
GRADIO_CONCURRENCY = 1

# VRAM の空き確認。Qwen や別の GPU サービスが載っていると起動しない。ここは「ほぼ空」を要求する閾値。
VRAM_FREE_THRESHOLD_MIB = 1024
VRAM_WAIT_SECONDS = 60


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def serve_options_from_env(environ: Mapping[str, str]) -> tuple[str, bool]:
    """環境変数から (dtype, flash-attn を使うか) を決める純粋関数。

    dtype が未設定・空白・知らない値なら既定（bfloat16）。flash-attn は既定で使わない
    （flash-attn のパッケージを入れていないため。上流の画面は既定で使おうとするので、必ず明示して渡す）。
    """

    dtype = (environ.get(DTYPE_ENV_VAR) or "").strip() or DEFAULT_DTYPE
    if dtype not in ALLOWED_DTYPES:
        dtype = DEFAULT_DTYPE
    return dtype, _truthy(environ.get(FLASH_ATTN_ENV_VAR))


def runtime_env_overrides() -> dict[str, str]:
    """キャッシュの置き場を models/ の下へ向け、利用状況を外へ送らせない環境変数（純粋関数）。"""

    return {
        "HF_HOME": str(HF_HOME_DIR),
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "GRADIO_ANALYTICS_ENABLED": "False",
    }
