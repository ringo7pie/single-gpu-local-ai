#!/usr/bin/env bash
# A30 サーバーで AudioCraft（MusicGen Medium / AudioGen Medium。tools/audiocraft、ポート 7862）を導入する。
# 冪等。再実行は既存物を触らない。
#
# torch（cu121。venv で約 5.5GB）と重み・T5 約 8.2GB をダウンロードする。実行前に空きディスクとネットワークを確認すること。
# systemd ユニットは配置と daemon-reload だけを行い、enable（自動起動）はしない
# （GPU を使うサービスは入口ページ tools/portal から使うときだけ起動する方針）。
set -euo pipefail

TOOL_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
VENV_DIR="$TOOL_DIR/.venv"
VENDOR_DIR="$TOOL_DIR/vendor/audiocraft"
RUNNER="$TOOL_DIR/run-audiocraft"

# 雛形の書き出し（render_unit_file）と、終わりに表示する URL のホスト（detect_server_host）
# shellcheck source=../../scripts/lib/render-unit.sh
source "$TOOL_DIR/../../scripts/lib/render-unit.sh"
# shellcheck source=../../scripts/lib/server-host.sh
source "$TOOL_DIR/../../scripts/lib/server-host.sh"

# acraft/config.py の UPSTREAM_URL / UPSTREAM_COMMIT と一致させること
# （venv 構築前に git 操作をするため、ここでは python を経由せず直値で持つ）。
UPSTREAM_URL="https://github.com/facebookresearch/audiocraft.git"
UPSTREAM_COMMIT="896ec7c47f5e5d1e5aa1e4b260c4405328bf009d"

# torch は上流が 2.1.0 に固定している（requirements.txt）。cu121 の index に cp311 の wheel があることを
# 2026-10-27 に確認した。requirements.lock.txt には書かない（index が別のため）。
# wheel が CUDA 12.1 の runtime を同梱するので、システムの CUDA Toolkit（13.2 / 12.6）は使わない。
TORCH_INDEX_URL="https://download.pytorch.org/whl/cu121"
TORCH_VERSION="2.1.0+cu121"
TORCH_SPEC="torch==2.1.0+cu121"
TORCHAUDIO_SPEC="torchaudio==2.1.0+cu121"
TORCHVISION_SPEC="torchvision==0.16.0+cu121"

usage() {
  cat <<USAGE
使い方: $(basename -- "$0") [オプション]

  --skip-models     重みの取得を省略する（あとで run-audiocraft fetch-models を手で実行する）
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

echo "== AudioCraft 導入（A30 サーバー） =="

# ---------------------------------------------------------------- [1/8]
echo
echo "[1/8] uv と venv（Python 3.11）"
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

# torch 2.1.0 の wheel は Python 3.11 までしか無い（cp312 が無い）。
if [ -d "$VENV_DIR" ]; then
  echo "venv: 既にあります（$VENV_DIR）"
else
  "$UV_BIN" venv --python 3.11 "$VENV_DIR"
  echo "venv: 作成しました（$VENV_DIR）"
fi

# ---------------------------------------------------------------- [2/8]
echo
echo "[2/8] 上流の取得（AudioCraft $UPSTREAM_COMMIT）"
if [ -d "$VENDOR_DIR/.git" ]; then
  current_head="$(git -C "$VENDOR_DIR" rev-parse HEAD 2>/dev/null || echo "")"
  if [ "$current_head" = "$UPSTREAM_COMMIT" ]; then
    echo "AudioCraft: 既にあります（$VENDOR_DIR, HEAD=$current_head）"
  else
    echo "AudioCraft は既にありますが、指定コミットではありません（HEAD=$current_head）。" >&2
    echo "勝手に張り替えないで終了します。確認して手動で対処してください: $VENDOR_DIR" >&2
    exit 1
  fi
else
  mkdir -p "$(dirname -- "$VENDOR_DIR")"
  git init "$VENDOR_DIR"
  git -C "$VENDOR_DIR" remote add origin "$UPSTREAM_URL"
  git -C "$VENDOR_DIR" fetch --depth 1 origin "$UPSTREAM_COMMIT"
  git -C "$VENDOR_DIR" checkout FETCH_HEAD
  echo "AudioCraft: 取得しました（$VENDOR_DIR）"
fi

# 上流への当て物があれば、固定コミットに対して当てる（今のところ無い）。
PATCH_DIR="$TOOL_DIR/patches"
if [ -d "$PATCH_DIR" ]; then
  for patch_file in "$PATCH_DIR"/*.patch
  do
    [ -e "$patch_file" ] || continue
    patch_name="$(basename -- "$patch_file")"
    if git -C "$VENDOR_DIR" apply --reverse --check "$patch_file" >/dev/null 2>&1; then
      echo "パッチ $patch_name: 既に当たっています"
    elif git -C "$VENDOR_DIR" apply --check "$patch_file" >/dev/null 2>&1; then
      git -C "$VENDOR_DIR" apply "$patch_file"
      echo "パッチ $patch_name: 当てました"
    else
      echo "パッチ $patch_name を当てられません。上流の内容を確認してください。" >&2
      exit 1
    fi
  done
fi

# ---------------------------------------------------------------- [3/8]
echo
echo "[3/8] torch（$TORCH_INDEX_URL）"
installed_torch="$("$VENV_DIR/bin/python" -c "import torch; print(torch.__version__)" 2>/dev/null || true)"
if [ "$installed_torch" = "$TORCH_VERSION" ]; then
  echo "torch: 既にあります（$installed_torch）"
else
  "$UV_BIN" pip install --python "$VENV_DIR/bin/python" \
    --index-url "$TORCH_INDEX_URL" \
    "$TORCH_SPEC" "$TORCHAUDIO_SPEC" "$TORCHVISION_SPEC"
  echo "torch: 導入しました（$TORCH_SPEC, $TORCHAUDIO_SPEC, $TORCHVISION_SPEC）"
fi

# ---------------------------------------------------------------- [4/8]
echo
echo "[4/8] 依存（requirements.lock.txt）"
"$UV_BIN" pip install --python "$VENV_DIR/bin/python" -r "$TOOL_DIR/requirements.lock.txt"
echo "依存: 導入しました"

# ---------------------------------------------------------------- [5/8]
echo
echo "[5/8] AudioCraft 本体と自前パッケージ（acraft）"
# --no-deps: 上流の requirements.txt は torch 2.1.0 などを厳密に固定しており、そのまま解決させると
# [3/8][4/8] で入れたものを置き換えかねない。依存は requirements.lock.txt で足りている。
"$UV_BIN" pip install --python "$VENV_DIR/bin/python" --no-deps -e "$VENDOR_DIR"
"$UV_BIN" pip install --python "$VENV_DIR/bin/python" -e "$TOOL_DIR"
echo "audiocraft・acraft: 導入しました"

# ---------------------------------------------------------------- [6/8]
echo
echo "[6/8] 重みの取得"
if $SKIP_MODELS; then
  echo "重み取得: 省略しました（--skip-models）。あとで '$RUNNER fetch-models' を実行してください"
else
  "$RUNNER" fetch-models
  echo "重み取得: 完了しました"
fi

# ---------------------------------------------------------------- [7/8]
echo
echo "[7/8] systemd user ユニットと env ファイル（enable はしない）"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SYSTEMD_USER_DIR/audiocraft.service"
if [ -f "$SERVICE_FILE" ]; then
  echo "systemd ユニット: 既にあります（$SERVICE_FILE）"
else
  mkdir -p "$SYSTEMD_USER_DIR"
  # 雛形のリポジトリのパス（@REPO_ROOT@）をこの clone の絶対パスにして置く
  render_unit_file "$TOOL_DIR/systemd/audiocraft.service.example" "$SERVICE_FILE"
  echo "systemd ユニット: 作成しました（$SERVICE_FILE）"
fi

ENV_DIR="$HOME/.config/audiocraft"
ENV_FILE="$ENV_DIR/audiocraft.env"
if [ -f "$ENV_FILE" ]; then
  echo "env ファイル: 既にあります（$ENV_FILE）"
else
  mkdir -p "$ENV_DIR"
  cat > "$ENV_FILE" <<'ENVFILE'
# AudioCraft の設定。systemd の EnvironmentFile として読み込まれるため、反映にはサービスの再起動が要る。
# 使うモデルを差し替えるときだけ、下の行の先頭の # を外して書き換える（既定は下の値）。
# 差し替えたら、run-audiocraft fetch-models で重みを取得し直すこと。
# ACRAFT_MUSIC_MODEL=facebook/musicgen-medium
# ACRAFT_SOUND_MODEL=facebook/audiogen-medium
ENVFILE
  chmod 644 "$ENV_FILE"
  echo "env ファイル: 作成しました（$ENV_FILE。既定のモデルのまま）"
fi

systemctl --user daemon-reload
echo "自動起動: 設定していません（enable しない方針）"
echo "使うときだけ入口ページから起動する（または systemctl --user start audiocraft）"

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
echo "  - 入口ページ経由で起動: http://$SERVER_HOST:8080（tools/portal を導入した場合。--port で変えたときはそのポート）"
echo "  - 手動起動: systemctl --user start audiocraft（ほかの GPU サービスは Conflicts= で止まる）"
echo "  - ブラウザ（直接）: http://$SERVER_HOST:7862"
echo "  - 他の PC から使うには、サーバーで ufw にポート 7862 を足す（knowledge/10-silver/resources/lan-access.md）"
exit "$DOCTOR_RC"
