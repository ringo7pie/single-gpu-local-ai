"""パス・ポート・モデル・上流のコミットを一元管理する。

このモジュールはファイルシステムに書き込まず、ネットワークにも触れない。
定数を並べるだけなので、素の python からでも torch なしで import できる。
"""

from __future__ import annotations

from pathlib import Path

# このファイルは tools/stable-audio/saudio/config.py なので、2 つ上が tools/stable-audio。
TOOL_DIR = Path(__file__).resolve().parents[1]

VENDOR_DIR = TOOL_DIR / "vendor" / "stable-audio-3"
MODELS_DIR = TOOL_DIR / "models"
OUTPUTS_DIR = TOOL_DIR / "outputs"
VENV_PYTHON = TOOL_DIR / ".venv" / "bin" / "python"

# 重みと実行時キャッシュは、すべて models/ の下に閉じ込める（Open WebUI と共有している ~/.cache に混ぜない）。
HF_HOME_DIR = MODELS_DIR / "hf"
HF_HUB_CACHE_DIR = HF_HOME_DIR / "hub"
TORCH_HOME_DIR = MODELS_DIR / "torch"

HOST = "0.0.0.0"
PORT = 7864
SERVICE_UNIT = "stable-audio"
SERVICE_FILE = Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_UNIT}.service"
CONFIG_DIR = Path.home() / ".config" / "stable-audio"
ENV_FILE = CONFIG_DIR / "stable-audio.env"
# Hugging Face のトークン（gated の重みを取るときだけ使う。中身はログ・json・README に書かない）。
HF_TOKEN_FILE = CONFIG_DIR / "hf-token"

OLLAMA_URL = "http://127.0.0.1:11434"

# 上流（Stability-AI/stable-audio-3）。2026-11-23 時点の main の先頭（2026-11-02）。タグは無い。
# setup-server.sh の UPSTREAM_URL / UPSTREAM_COMMIT と必ず一致させる（試験で確かめる）。
UPSTREAM_URL = "https://github.com/Stability-AI/stable-audio-3.git"
UPSTREAM_COMMIT = "790793b89ddceea612be9ce42ff6830d18f6fa57"

# 上流の model_configs.py の名前と、その Hugging Face のリポジトリ（gated。規約への同意とトークンが要る）。
MODEL_NAME = "medium"
MODEL_REPO = "stabilityai/stable-audio-3-medium"
# 取得するファイル。文章の符号化器（T5Gemma）は同じリポジトリの t5gemma-b-b-ul2/ にある。
MODEL_FILES = ("model_config.json", "model.safetensors")
TEXT_ENCODER_SUBFOLDER = "t5gemma-b-b-ul2"

# VRAM の空き確認。Qwen や別の GPU サービスが載っていると起動しない。
VRAM_FREE_THRESHOLD_MIB = 1024
VRAM_WAIT_SECONDS = 60

# REST の口が、生成の順番待ちを含めて同時に受け付ける依頼の数。超えたら 503（Retry-After 付き）。
REST_MAX_PENDING = 4
REST_RETRY_AFTER_SEC = 30


def runtime_env_overrides(offline: bool = True):
    """重み・キャッシュの置き場を models/ の下へ向ける環境変数（純粋関数）。

    torch / transformers / huggingface_hub を import する前に環境へ入れる必要がある。
    offline が真なら HF_HUB_OFFLINE=1 にして、起動のたびに Hugging Face へ問い合わせない
    （重みは fetch-models で取ってあり、gated なのでトークン無しでは問い合わせが失敗する）。
    """

    env = {
        "HF_HOME": str(HF_HOME_DIR),
        "HF_HUB_CACHE": str(HF_HUB_CACHE_DIR),
        "TORCH_HOME": str(TORCH_HOME_DIR),
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "GRADIO_ANALYTICS_ENABLED": "False",
    }
    if offline:
        env["HF_HUB_OFFLINE"] = "1"
    return env


# 重みのリビジョン（Hugging Face のコミット）。2026-11-23 に API で見た main の先頭（lastModified 2026-10-06）。
# 上流のコードは revision を指定せず main を読むので、fetch-models はこのコミットを取ったうえで、
# キャッシュの refs/main をこのコミットに向ける（huggingface_hub がブランチ名で取ったときに書くのと同じ形）。
MODEL_REVISION = "d4d3579395a8ac0df4ab13b5b1d7138ce7290f81"
