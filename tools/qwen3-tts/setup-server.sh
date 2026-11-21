#!/usr/bin/env bash
# A30 サーバーで Qwen3-TTS VoiceDesign（tools/qwen3-tts、ポート 7863）を導入する。
# 冪等。再実行は既存物を触らない。
#
# torch（cu126。venv で数 GB）と重み 約 4.3 GB をダウンロードする。実行前に空きディスクとネットワークを確認すること。
# systemd ユニットは配置と daemon-reload だけを行い、enable（自動起動）はしない
# （GPU を使うサービスは入口ページ tools/portal から使うときだけ起動する方針）。
set -euo pipefail

TOOL_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
VENV_DIR="$TOOL_DIR/.venv"
RUNNER="$TOOL_DIR/run-qwen3-tts"

# 雛形の書き出し（render_unit_file）と、終わりに表示する URL のホスト（detect_server_host）
# shellcheck source=../../scripts/lib/render-unit.sh
source "$TOOL_DIR/../../scripts/lib/render-unit.sh"
# shellcheck source=../../scripts/lib/server-host.sh
source "$TOOL_DIR/../../scripts/lib/server-host.sh"

# 上流（PyPI の qwen-tts）は torch の版を指定しない。transformers==4.57.3 に固定しているので、
# ComfyUI（tools/comfyui）と同じ torch 2.9.1 の cu126 にする。wheel が CUDA 12.6 の runtime を同梱するので、
# システムの CUDA Toolkit は使わない。requirements.lock.txt には書かない（index が別のため）。
TORCH_INDEX_URL="https://download.pytorch.org/whl/cu126"
TORCH_VERSION="2.9.1+cu126"
TORCH_SPEC="torch==2.9.1+cu126"
TORCHAUDIO_SPEC="torchaudio==2.9.1+cu126"

usage() {
  cat <<USAGE
使い方: $(basename -- "$0") [オプション]

  --skip-models     重みの取得を省略する（あとで run-qwen3-tts fetch-models を手で実行する）
  --skip-gpu-check  最後の点検（doctor）で GPU を触らない（ほかのプロセスが GPU を使っている間の導入用）
  -h, --help        この説明を表示して終了
USAGE
}

SKIP_MODELS=false
SKIP_GPU_CHECK=false
while [ $# -gt 0 ]
do
  case "$1" in
    --skip-models)
      SKIP_MODELS=true
      shift
      ;;
    --skip-gpu-check)
      SKIP_GPU_CHECK=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "未知の引数: $1" >&2
      usage
      exit 2
      ;;
  esac
done

echo "== Qwen3-TTS VoiceDesign 導入（A30 サーバー） =="

# ---------------------------------------------------------------- [1/6]
echo
echo "[1/6] uv と venv（Python 3.12）"
if [ -x "$HOME/.local/bin/uv" ]; then
  UV_BIN="$HOME/.local/bin/uv"
elif command -v uv >/dev/null 2>&1; then
  UV_BIN="$(command -v uv)"
else
  echo "uv がありません。導入方法:" >&2
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  echo "導入後にこのスクリプトを再実行してください。" >&2
  exit 2
fi

if [ -d "$VENV_DIR" ]; then
  echo "venv: 既にあります（$VENV_DIR）"
else
  "$UV_BIN" venv --python 3.12 "$VENV_DIR"
  echo "venv: 作成しました（$VENV_DIR）"
fi

# ---------------------------------------------------------------- [2/6]
echo
echo "[2/6] torch（$TORCH_INDEX_URL）"
installed_torch="$("$VENV_DIR/bin/python" -c "import torch; print(torch.__version__)" 2>/dev/null || true)"
if [ "$installed_torch" = "$TORCH_VERSION" ]; then
  echo "torch: 既にあります（$installed_torch）"
else
  "$UV_BIN" pip install --python "$VENV_DIR/bin/python" \
    --index-url "$TORCH_INDEX_URL" \
    "$TORCH_SPEC" "$TORCHAUDIO_SPEC"
  echo "torch: 導入しました（$TORCH_SPEC, $TORCHAUDIO_SPEC）"
fi

# ---------------------------------------------------------------- [3/6]
echo
echo "[3/6] qwen-tts と依存（requirements.lock.txt）と自前パッケージ（qtts）"
"$UV_BIN" pip install --python "$VENV_DIR/bin/python" -r "$TOOL_DIR/requirements.lock.txt"
"$UV_BIN" pip install --python "$VENV_DIR/bin/python" -e "$TOOL_DIR"
echo "依存: 導入しました"

# ---------------------------------------------------------------- [4/6]
echo
echo "[4/6] 重みの取得"
if $SKIP_MODELS; then
  echo "重み取得: 省略しました（--skip-models）。あとで '$RUNNER fetch-models' を実行してください"
else
  "$RUNNER" fetch-models
  echo "重み取得: 完了しました"
fi

# ---------------------------------------------------------------- [5/6]
echo
echo "[5/6] systemd user ユニットと env ファイル（enable はしない）"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SYSTEMD_USER_DIR/qwen3-tts.service"
if [ -f "$SERVICE_FILE" ]; then
  echo "systemd ユニット: 既にあります（$SERVICE_FILE）"
else
  mkdir -p "$SYSTEMD_USER_DIR"
  # 雛形のリポジトリのパス（@REPO_ROOT@）をこの clone の絶対パスにして置く
  render_unit_file "$TOOL_DIR/systemd/qwen3-tts.service.example" "$SERVICE_FILE"
  echo "systemd ユニット: 作成しました（$SERVICE_FILE）"
fi

ENV_DIR="$HOME/.config/qwen3-tts"
ENV_FILE="$ENV_DIR/qwen3-tts.env"
if [ -f "$ENV_FILE" ]; then
  echo "env ファイル: 既にあります（$ENV_FILE）"
else
  mkdir -p "$ENV_DIR"
  cat > "$ENV_FILE" <<'ENVFILE'
# Qwen3-TTS の設定。systemd の EnvironmentFile として読み込まれるため、反映にはサービスの再起動が要る。
# 変えるときだけ、下の行の先頭の # を外して書き換える（既定は下の値）。
# 重みの型（bfloat16 / float16 / float32）
# QWEN3TTS_DTYPE=bfloat16
# FlashAttention 2 を使う（1）。flash-attn のパッケージを venv に入れていないので、既定は使わない（0）
# QWEN3TTS_FLASH_ATTN=0
ENVFILE
  chmod 644 "$ENV_FILE"
  echo "env ファイル: 作成しました（$ENV_FILE。既定のまま）"
fi

systemctl --user daemon-reload
echo "自動起動: 設定していません（enable しない方針）"
echo "使うときだけ入口ページから起動する（または systemctl --user start qwen3-tts）"

# ---------------------------------------------------------------- [6/6]
echo
echo "[6/6] 点検"
DOCTOR_RC=0
if $SKIP_GPU_CHECK; then
  "$RUNNER" doctor --skip-gpu || DOCTOR_RC=$?
else
  "$RUNNER" doctor || DOCTOR_RC=$?
fi
if [ "$DOCTOR_RC" -eq 0 ]; then
  echo "点検: 合格しました"
else
  echo "点検: 一部 NG があります（終了コード $DOCTOR_RC）。上の出力を確認してください"
fi

SERVER_HOST="$(detect_server_host)"
echo
echo "次にやること:"
echo "  - 入口ページ経由で起動: http://$SERVER_HOST:8080（tools/portal を導入した場合。--port で変えたときはそのポート）"
echo "  - 手動起動: systemctl --user start qwen3-tts（ほかの GPU サービスは Conflicts= で止まる）"
echo "  - ブラウザ（直接）: http://$SERVER_HOST:7863"
echo "  - 他の PC から使うには、サーバーで ufw にポート 7863 を足す（knowledge/10-silver/resources/lan-access.md）"
exit "$DOCTOR_RC"
