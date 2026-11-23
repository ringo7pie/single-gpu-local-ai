#!/usr/bin/env bash
# A30 サーバーで Stable Audio 3.0 Medium（tools/stable-audio、ポート 7864）を導入する。
# 冪等。再実行は既存物を触らない。
#
# torch（cu126。venv で約 6 GB）と重み約 10.5 GB をダウンロードする。重みは Hugging Face で規約に同意した
# アカウントのトークンが要る（gated。手順は run-stable-audio fetch-models が表示する）。
# systemd ユニットは配置と daemon-reload だけを行い、enable（自動起動）はしない
# （GPU を使うサービスは入口ページ tools/portal から使うときだけ起動する方針）。
set -euo pipefail

TOOL_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
VENV_DIR="$TOOL_DIR/.venv"
VENDOR_DIR="$TOOL_DIR/vendor/stable-audio-3"
RUNNER="$TOOL_DIR/run-stable-audio"

# shellcheck source=../../scripts/lib/render-unit.sh
source "$TOOL_DIR/../../scripts/lib/render-unit.sh"
# shellcheck source=../../scripts/lib/server-host.sh
source "$TOOL_DIR/../../scripts/lib/server-host.sh"

# saudio/config.py の UPSTREAM_URL / UPSTREAM_COMMIT と一致させること
# （venv 構築前に git 操作をするため、ここでは python を経由せず直値で持つ）。
UPSTREAM_URL="https://github.com/Stability-AI/stable-audio-3.git"
UPSTREAM_COMMIT="790793b89ddceea612be9ce42ff6830d18f6fa57"

# torch は上流が 2.7.1 に固定し、既定の index を cu126 にしている（pyproject.toml）。wheel が CUDA 12.6 の runtime を
# 同梱するので、システムの CUDA Toolkit（13.2 / 12.6）は使わない。requirements.lock.txt には書かない（index が別のため）。
PYTHON_VERSION="3.12"
TORCH_INDEX_URL="https://download.pytorch.org/whl/cu126"
TORCH_VERSION="2.7.1+cu126"
TORCH_SPEC="torch==2.7.1+cu126"
TORCHAUDIO_SPEC="torchaudio==2.7.1+cu126"
# flash-attn は Medium の音声圧縮モデル（SAME-L）が必須とする（上流 docs/workflows/inference.md）。PyPI の sdist は
# ソースからのビルドになる（数十分〜）ので、公式リリースの wheel を使う。torch 2.7.1+cu126 は CXX11 ABI が真
# （torch._C._GLIBCXX_USE_CXX11_ABI を 2026-11-23 に確かめた）なので cxx11abiTRUE の cp312 を選ぶ。A30（sm_80）を含む。
FLASH_ATTN_VERSION="2.8.3"
FLASH_ATTN_WHEEL_URL="https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.3/flash_attn-2.8.3+cu12torch2.7cxx11abiTRUE-cp312-cp312-linux_x86_64.whl"

usage() {
  cat <<USAGE
使い方: $(basename -- "$0") [オプション]

  --skip-models     重みの取得を省略する（あとで run-stable-audio fetch-models を手で実行する）
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

echo "== Stable Audio 3.0 Medium 導入（A30 サーバー） =="

# ---------------------------------------------------------------- [1/8]
echo
echo "[1/8] uv と venv（Python $PYTHON_VERSION）"
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
  "$UV_BIN" venv --python "$PYTHON_VERSION" "$VENV_DIR"
  echo "venv: 作成しました（$VENV_DIR）"
fi

# ---------------------------------------------------------------- [2/8]
echo
echo "[2/8] 上流の取得（stable-audio-3 $UPSTREAM_COMMIT）"
if [ -d "$VENDOR_DIR/.git" ]; then
  current_head="$(git -C "$VENDOR_DIR" rev-parse HEAD 2>/dev/null || echo "")"
  if [ "$current_head" = "$UPSTREAM_COMMIT" ]; then
    echo "stable-audio-3: 既にあります（$VENDOR_DIR, HEAD=$current_head）"
  else
    echo "stable-audio-3 は既にありますが、指定コミットではありません（HEAD=$current_head）。" >&2
    echo "勝手に張り替えないで終了します。確認して手動で対処してください: $VENDOR_DIR" >&2
    exit 1
  fi
else
  mkdir -p "$(dirname -- "$VENDOR_DIR")"
  git init "$VENDOR_DIR"
  git -C "$VENDOR_DIR" remote add origin "$UPSTREAM_URL"
  git -C "$VENDOR_DIR" fetch --depth 1 origin "$UPSTREAM_COMMIT"
  git -C "$VENDOR_DIR" checkout FETCH_HEAD
  echo "stable-audio-3: 取得しました（$VENDOR_DIR）"
fi

# ---------------------------------------------------------------- [3/8]
echo
echo "[3/8] torch（$TORCH_INDEX_URL）"
installed_torch="$("$VENV_DIR/bin/python" -c "import torch; print(torch.__version__)" 2>/dev/null || true)"
if [ "$installed_torch" = "$TORCH_VERSION" ]; then
  echo "torch: 既にあります（$installed_torch）"
else
  "$UV_BIN" pip install --python "$VENV_DIR/bin/python" --index-url "$TORCH_INDEX_URL" "$TORCH_SPEC" "$TORCHAUDIO_SPEC"
  echo "torch: 導入しました（$TORCH_SPEC, $TORCHAUDIO_SPEC）"
fi

# ---------------------------------------------------------------- [4/8]
echo
echo "[4/8] 依存（requirements.lock.txt）"
"$UV_BIN" pip install --python "$VENV_DIR/bin/python" -r "$TOOL_DIR/requirements.lock.txt"
echo "依存: 導入しました"

# ---------------------------------------------------------------- [5/8]
echo
echo "[5/8] 上流（stable-audio-3）・flash-attn・自前パッケージ（saudio）"
# --no-deps: 上流の pyproject.toml の依存は [3/8][4/8] で入れてある（torch を別の index から置き換えさせない）。
"$UV_BIN" pip install --python "$VENV_DIR/bin/python" --no-deps -e "$VENDOR_DIR"
installed_flash="$("$VENV_DIR/bin/python" -c "import importlib.metadata as m; print(m.version('flash-attn'))" 2>/dev/null || true)"
case "$installed_flash" in
  "$FLASH_ATTN_VERSION"*)
    echo "flash-attn: 既にあります（$installed_flash）"
    ;;
  *)
    "$UV_BIN" pip install --python "$VENV_DIR/bin/python" --no-deps "$FLASH_ATTN_WHEEL_URL"
    echo "flash-attn: 導入しました（$FLASH_ATTN_VERSION）"
    ;;
esac
"$UV_BIN" pip install --python "$VENV_DIR/bin/python" -e "$TOOL_DIR"
echo "stable-audio-3・flash-attn・saudio: 導入しました"

# ---------------------------------------------------------------- [6/8]
echo
echo "[6/8] 重みの取得（gated）"
if $SKIP_MODELS; then
  echo "重み取得: 省略しました（--skip-models）。あとで '$RUNNER fetch-models' を実行してください"
else
  FETCH_RC=0
  "$RUNNER" fetch-models || FETCH_RC=$?
  if [ "$FETCH_RC" -eq 3 ]; then
    echo "重み取得: トークンが無いため省略しました。上の手順でトークンを置いてから '$RUNNER fetch-models' を実行してください"
  elif [ "$FETCH_RC" -ne 0 ]; then
    echo "重み取得: 失敗しました（終了コード $FETCH_RC）" >&2
    exit "$FETCH_RC"
  else
    echo "重み取得: 完了しました"
  fi
fi

# ---------------------------------------------------------------- [7/8]
echo
echo "[7/8] systemd user ユニットと env ファイル（enable はしない）"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SYSTEMD_USER_DIR/stable-audio.service"
if [ -f "$SERVICE_FILE" ]; then
  echo "systemd ユニット: 既にあります（$SERVICE_FILE）"
else
  mkdir -p "$SYSTEMD_USER_DIR"
  render_unit_file "$TOOL_DIR/systemd/stable-audio.service.example" "$SERVICE_FILE"
  echo "systemd ユニット: 作成しました（$SERVICE_FILE）"
fi

ENV_DIR="$HOME/.config/stable-audio"
ENV_FILE="$ENV_DIR/stable-audio.env"
if [ -f "$ENV_FILE" ]; then
  echo "env ファイル: 既にあります（$ENV_FILE）"
else
  mkdir -p "$ENV_DIR"
  chmod 700 "$ENV_DIR"
  cat > "$ENV_FILE" <<'ENVFILE'
# Stable Audio 3.0 Medium の設定。systemd の EnvironmentFile として読み込まれるため、反映にはサービスの再起動が要る。
# いまのところ設定する値は無い。Hugging Face のトークンはここに書かず、同じフォルダの hf-token（600）に置く。
ENVFILE
  chmod 644 "$ENV_FILE"
  echo "env ファイル: 作成しました（$ENV_FILE）"
fi

systemctl --user daemon-reload
echo "自動起動: 設定していません（enable しない方針）"
echo "使うときだけ入口ページから起動する（または systemctl --user start stable-audio）"

# ---------------------------------------------------------------- [8/8]
echo
echo "[8/8] 点検"
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
echo "  - 入口ページ経由で起動: http://$SERVER_HOST:8080（tools/portal を導入した場合）"
echo "  - 手動起動: systemctl --user start stable-audio（ほかの GPU サービスは Conflicts= で止まる）"
echo "  - ブラウザ（直接）: http://$SERVER_HOST:7864"
echo "  - 他の PC から使うには、サーバーで ufw にポート 7864 を足す（knowledge/10-silver/resources/lan-access.md）"
exit "$DOCTOR_RC"
