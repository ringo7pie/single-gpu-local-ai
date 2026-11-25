#!/usr/bin/env bash
# 他の PC（Linux / macOS）で実行する。localai-mcp を Claude Code / Codex に登録し、A30 サーバーの AI を使えるようにする。
#
# 使い方:  bash setup-remote.sh <サーバーのIP> [--skip-check]
#   例: bash setup-remote.sh 192.168.1.10
#   サーバーの IP は環境変数 LOCALAI_SERVER でも指定できる。
#
# やること（何度実行してもよい。2 回目以降は登録を新しい値で入れ直す）:
#   1. localai_mcp.py を ~/.local/share/localai-mcp/ に写す（サーバーの共有が外れても動くように）。
#      このファイルの隣（../localai_mcp.py）に無ければ、入口ページ（http://<サーバー>:8080/remote/localai_mcp.py）から取る。
#      入口ページから直接流す例: curl -fsSL http://<サーバー>:8080/remote/setup-remote.sh | bash -s -- <サーバー>
#   2. 起動に使う Python を決める（python3 が 3.10 以上ならそれ、無ければ uv run --python 3.12）
#   3. claude / codex があれば MCP「localai」として登録し、タイムアウトを長くする
#   4. 疎通確認: 入口ページ（8080）と、稼働中のサービスのポートに届くか
set -euo pipefail

server="${LOCALAI_SERVER:-}"
skip_check=0
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-check) skip_check=1; shift ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) server="$1"; shift ;;
  esac
done
if [ -z "$server" ]; then
  echo "サーバーの IP が未指定です。引数か環境変数 LOCALAI_SERVER で指定してください（例: 192.168.1.10）。" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-.}")" && pwd)"
source_file="$script_dir/../localai_mcp.py"
install_dir="${LOCALAI_MCP_HOME:-$HOME/.local/share/localai-mcp}"
target_file="$install_dir/localai_mcp.py"

# 1. 写す（隣に無ければ入口ページから取る）
mkdir -p "$install_dir"
if [ -f "$source_file" ]; then
  cp "$source_file" "$target_file"
  echo "[1/4] $target_file に写しました。"
else
  remote_url="http://$server:${LOCALAI_PORTAL_PORT:-8080}/remote/localai_mcp.py"
  if ! curl -fsSL --max-time 60 -o "$target_file.part" "$remote_url"; then
    echo "localai_mcp.py を取れません: $remote_url（入口ページが動いているか、IP が正しいかを確かめてください）" >&2
    rm -f "$target_file.part"
    exit 1
  fi
  mv "$target_file.part" "$target_file"
  echo "[1/4] $remote_url から $target_file に取りました。"
fi

# 2. Python
python_ok() {
  "$@" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}
launcher=()
if command -v python3 >/dev/null 2>&1 && python_ok python3; then
  launcher=("$(command -v python3)" "$target_file")
  echo "[2/4] 起動は python3（$(python3 --version 2>&1)）。"
elif command -v uv >/dev/null 2>&1; then
  launcher=("$(command -v uv)" run --no-project --python 3.12 "$target_file")
  echo "[2/4] python3 が 3.10 未満か無いので、uv run --python 3.12 で起動します（初回は uv が Python を取得します）。"
else
  echo "[2/4] Python 3.10 以上も uv もありません。どちらかを入れてから再実行してください。" >&2
  echo "      uv: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

# 3. MCP 登録
if command -v claude >/dev/null 2>&1; then
  claude mcp remove -s user localai >/dev/null 2>&1 || true
  # -e は値を何個でも取るので、名前（localai）を先に置く。後ろに置くと名前まで環境変数として読まれる。
  claude mcp add -s user localai -e "LOCALAI_SERVER=$server" -- "${launcher[@]}"
  echo "[3/4] Claude Code: localai を登録しました（user スコープ）。"
  settings="$HOME/.claude/settings.json"
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$settings" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1]); path.parent.mkdir(parents=True, exist_ok=True)
data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
env = data.setdefault("env", {})
changed = False
# 生成は数分〜30 分かかる。既に長い値が入っていれば短くしない。
for key, value in (("MCP_TOOL_TIMEOUT", 2100000), ("MCP_TIMEOUT", 30000)):
    try:
        current = int(env.get(key, 0))
    except (TypeError, ValueError):
        current = 0
    if current < value:
        env[key] = str(value); changed = True
if changed:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("      settings.json の env に MCP_TOOL_TIMEOUT / MCP_TIMEOUT を足しました（他の設定は無傷）。")
else:
    print("      settings.json のタイムアウトは既に足りています。")
PY
  else
    echo "      python3 が無いため settings.json は手で編集: env に MCP_TOOL_TIMEOUT=2100000, MCP_TIMEOUT=30000"
  fi
else
  echo "[3/4] claude コマンドが無いので Claude Code の登録は省略。"
fi

if command -v codex >/dev/null 2>&1; then
  codex mcp remove localai >/dev/null 2>&1 || true
  codex mcp add localai --env "LOCALAI_SERVER=$server" -- "${launcher[@]}"
  echo "      Codex: localai を登録しました。"
  codex_cfg="${CODEX_HOME:-$HOME/.codex}/config.toml"
  if [ -f "$codex_cfg" ] && command -v python3 >/dev/null 2>&1; then
    python3 - "$codex_cfg" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1]); text = path.read_text(encoding="utf-8")
head, sep, tail = text.partition("[mcp_servers.localai]\n")
if sep:
    body, marker, rest = tail.partition("\n[")
    if "tool_timeout_sec" not in body:
        body = body.rstrip("\n") + "\ntool_timeout_sec = 2100\nstartup_timeout_sec = 30\n"
        path.write_text(head + sep + body + (marker + rest if marker else ""), encoding="utf-8")
        print("      config.toml の [mcp_servers.localai] にタイムアウトを足しました。")
PY
  fi
else
  echo "      codex コマンドが無いので Codex の登録は省略。"
fi

# 4. 疎通確認
if [ "$skip_check" -eq 1 ]; then
  echo "[4/4] --skip-check 指定のため疎通確認を省きます。"
  exit 0
fi
echo "[4/4] 疎通確認:"
if LOCALAI_SERVER="$server" "${launcher[@]}" --check; then
  echo "完了。Claude Code / Codex を開き直してください。"
  echo "  確かめ方: Claude Code で /mcp と打ち、localai が connected になっていること（ターミナルなら claude mcp list）。"
  echo "  使い方: ツールはスラッシュコマンドには出ません。「localai の状態を見て」「localai_vfx で透明な火花のスプライトを作って」のように文で頼みます。"
else
  echo "届かないものがあります。上の NG の行を見てください（サーバーの ufw・サービスの起動）。登録は済んでいます。"
  exit 1
fi
