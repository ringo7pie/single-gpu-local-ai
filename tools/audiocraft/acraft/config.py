"""パス・ポート・モデル ID を一元管理する。

このモジュールはファイルシステムに書き込まず、ネットワークにも触れない。
定数を並べるだけなので、素の python からでも torch なしで import できる。
"""

from __future__ import annotations

from pathlib import Path

# このファイルは tools/audiocraft/acraft/config.py なので、2 つ上が tools/audiocraft。
TOOL_DIR = Path(__file__).resolve().parents[1]

VENDOR_DIR = TOOL_DIR / "vendor" / "audiocraft"
MODELS_DIR = TOOL_DIR / "models"
OUTPUTS_DIR = TOOL_DIR / "outputs"
VENV_PYTHON = TOOL_DIR / ".venv" / "bin" / "python"

# 重みと実行時キャッシュは、すべて models/ の下に閉じ込める。
# 既定のままだと Open WebUI と共有している ~/.cache に混ざる。
# AUDIOCRAFT_CACHE_DIR は audiocraft の loaders.py が hf_hub_download の cache_dir に使う環境変数。
AUDIOCRAFT_CACHE_DIR = MODELS_DIR / "audiocraft"
HF_HOME_DIR = MODELS_DIR / "hf"
TORCH_HOME_DIR = MODELS_DIR / "torch"

HOST = "0.0.0.0"
PORT = 7862
SERVICE_UNIT = "audiocraft"
SERVICE_FILE = Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_UNIT}.service"
ENV_FILE = Path.home() / ".config" / "audiocraft" / "audiocraft.env"

OLLAMA_URL = "http://127.0.0.1:11434"

# 上流（facebookresearch/audiocraft）。2026-10-27 時点の main の先頭（2025-03-13）。タグ v1.3.0 より新しく、
# 推論時にチェックポイントをローカルパスから読む修正（#539）を含む。以後上流の更新は無い。
# setup-server.sh の UPSTREAM_URL / UPSTREAM_COMMIT と必ず一致させる（試験で確かめる）。
UPSTREAM_URL = "https://github.com/facebookresearch/audiocraft.git"
UPSTREAM_COMMIT = "896ec7c47f5e5d1e5aa1e4b260c4405328bf009d"

# モデルの選択。env ファイル（EnvironmentFile）で差し替えられる。
MUSIC_MODEL_ENV_VAR = "ACRAFT_MUSIC_MODEL"
SOUND_MODEL_ENV_VAR = "ACRAFT_SOUND_MODEL"
DEFAULT_MUSIC_MODEL = "facebook/musicgen-medium"
DEFAULT_SOUND_MODEL = "facebook/audiogen-medium"

# audiocraft の loaders.py が Hugging Face から取る、言語モデルと音声圧縮モデルの重み（1 モデルにつき 2 ファイル）。
WEIGHT_FILES = ("state_dict.bin", "compression_state_dict.bin")

# 文章を符号化する T5。MusicGen / AudioGen のチェックポイント内の設定が名前を決める（medium はどちらも t5-base）。
# transformers が HF_HOME へ落とす。
TEXT_ENCODER_MODEL = "t5-base"

# VRAM の空き確認。Qwen や別の GPU サービスが載っていると起動しない。
# 2 モデルの重みは fp16 で各約 3〜4 GiB の見込み（実測で見直す）。ここは「ほぼ空」を要求する閾値。
VRAM_FREE_THRESHOLD_MIB = 1024
VRAM_WAIT_SECONDS = 60


def model_ids_from_env(environ):
    """環境変数から (音楽モデル ID, 効果音モデル ID) を決める純粋関数。

    未設定・空白だけの値は既定にする。ファイルシステムにもネットワークにも触れない。
    """

    music = (environ.get(MUSIC_MODEL_ENV_VAR) or "").strip() or DEFAULT_MUSIC_MODEL
    sound = (environ.get(SOUND_MODEL_ENV_VAR) or "").strip() or DEFAULT_SOUND_MODEL
    return music, sound


def runtime_env_overrides():
    """重み・キャッシュの置き場を models/ の下へ向ける環境変数（純粋関数）。

    audiocraft / transformers / torch を import する前に環境へ入れる必要がある。
    HF_HUB_DISABLE_TELEMETRY・GRADIO_ANALYTICS_ENABLED は、利用状況を外へ送らせないため。
    """

    return {
        "AUDIOCRAFT_CACHE_DIR": str(AUDIOCRAFT_CACHE_DIR),
        "HF_HOME": str(HF_HOME_DIR),
        "TORCH_HOME": str(TORCH_HOME_DIR),
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "GRADIO_ANALYTICS_ENABLED": "False",
    }

# REST の口（POST /api/v1/music・/api/v1/sound）が、生成の順番待ちを含めて同時に受け付ける依頼の数。
# 生成は 1 度に 1 件なので、超えた依頼には 503（Retry-After 付き）を返す。作曲 30 秒で約 33 秒かかるため、
# 4 件待つと最後の依頼は 2 分以上待つ。
REST_MAX_PENDING = 4
REST_RETRY_AFTER_SEC = 30
