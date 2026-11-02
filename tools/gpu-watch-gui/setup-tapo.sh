#!/usr/bin/env bash
# GPU Watch が「PC 全体」を Tapo プラグ（P110M）の実測で出すための、専用 venv を作る。
# システムの python3 には何も入れない。sudo は使わない。冪等（何度実行しても同じ結果）。
#
# 入れるもの: tapo（PyPI。P110M(JP) の暗号方式 TPAP に対応している。python-kasa は未対応）
# 置き場所:   ~/.local/share/tapo-meter/.venv（環境変数 TAPO_VENV で変えられる）
#
# 資格情報（TP-Link ID とパスワード）はここでは扱わない。GPU Watch のログイン画面
# （「Tapo」ボタン）で入力する。パスワードはファイルに保存されない。
set -euo pipefail

TAPO_VERSION="0.11.1"
VENV="${TAPO_VENV:-$HOME/.local/share/tapo-meter/.venv}"

mkdir -p -- "$(dirname -- "$VENV")"
if [ ! -x "$VENV/bin/python" ]; then
  if command -v uv >/dev/null 2>&1; then
    uv venv --python 3.12 "$VENV"
  else
    /usr/bin/python3 -m venv "$VENV"
  fi
fi

if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$VENV/bin/python" "tapo==$TAPO_VERSION"
else
  "$VENV/bin/python" -m pip install "tapo==$TAPO_VERSION"
fi

# 入っていて、読み込めることだけを確かめる（機器にはつながない）
"$VENV/bin/python" -I -c "from tapo import ApiClient; print('tapo を読み込めました')"

echo
echo "専用 venv: $VENV"
echo "次の手順: GPU Watch を起動し、ログイン画面（または「Tapo」ボタン）で"
echo "  プラグの IP・TP-Link ID・パスワードを入力する。"
