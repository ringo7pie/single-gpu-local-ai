# local-ai-tools

ローカル Qwen を Claude Code / Codex から使う道具（`tools/qwen-worker`、`tools/local-ai-bridge`、`tools/status`）に
ついて、コードから読み取れない運用上の事実。使い方の手順は `knowledge/30-gold/サーバーでの使い方マニュアル.html` と
`リモートで使い方マニュアル.html` を Read する。

## 導入

- A30 サーバー側の導入は `tools/local-ai-bridge/scripts/setup-server.sh` と `tools/qwen-worker/setup-server.sh` を
  各 1 回実行する。冪等で、既にあるものは触らない。2026-09-21 にこのサーバーで実行して確認済み。
- 他の PC 側は qwen-worker なら `tools/qwen-worker/remote/setup-remote.sh` / `.ps1`、local-ai-bridge なら
  `tools/local-ai-bridge/scripts/setup-workpc.sh` / `.ps1` を 1 回実行する。サーバー側の鍵受け入れはスクリプトが
  ssh 経由で行い、サーバーのログインパスワードを 1 回聞く。サーバーで人が打つのは鍵の取り消しだけ。
- setup-workpc.sh / .ps1 と Windows→WSL2 の bridge 経路は実機で未検証。local-ai-bridge は `fcntl.flock` を使うため
  Windows ネイティブでは動かない。
- PowerShell 7.3 以降では `ssh-keygen -N '""'` が文字列 `""` をパスフレーズにする。`--%`（停止解析）で渡す。
  PowerShell では `< NUL` のリダイレクトは使えない。疎通確認は `ssh -n -T qwen-worker`。

## local-ai-bridge の検証コマンド

- worktree には `.venv` が無い（gitignore）。`bridge.toml` の検証コマンドは venv の python を絶対パスで指す。
  相対パスにすると終了コード 127 で基準検証から失敗する。
- `--on-dirty include_uncommitted` は複製した未コミットファイルも差分に含めるため、`applies_to_original` が偽になる。
  委譲の前に作業ツリーをコミットして clean にし、`use_head` で渡す。
- Ollama は `num_ctx=65536` のとき入力を約 32,770 トークンで先頭から黙って切り捨てる。1 ファイルが大きい
  refactor（1,100 行のモジュールの切り出しなど）は Qwen には渡さない。qwen-worker の入力予算は 24,000 トークン。

## 費用と役割（2026-09-21 決定）

- Qwen へ委譲しても Claude Code のクラウド費用は減らない。round 1〜5 の実測で 委譲/単独（または Sonnet サブ）の比は
  0.81〜1.15、0.88、2.85。50% 以下は未観測。費用 ≒ 親のターン数 × 文脈長で、委譲は親のターンを 2〜3 より減らせない。
- 委譲の待ちは `run`（時間切れ）→ `wait` を親が刻むと高い。常駐ループ 1 本で待ち、結果ファイルの出現だけを見る。
- Qwen の実装は小さく仕様が明確なスクリプトなら合格するが、毎回 1 か所の補修が要った。レビュー担当としては有効。
- 利用者の決定: 連携層・鍵管理・状態確認・Ollama・Open WebUI は残す。役割は夜間バッチ、別モデルによるレビュー、
  ローカルに閉じた作業、他の PC からの利用。`/qwen` を実装の既定にしない。実装・調査のサブは Sonnet 5 xHigh。
- 詳細は `tools/local-ai-bridge/bench/results.json` と
  `knowledge/30-gold/Claude サブエージェントとしての Qwen 評価レポート.html` を Read する。

## 入口ページの AI を他の PC から使う（localai-mcp、2026-11-23）

- 他の PC の Claude Code / Codex からは MCP「localai」（`tools/localai-mcp`）で入口ページ（8080）の全サービスを起動・停止し、各 AI の API を呼ぶ。
  MCP は他の PC で動き、各サービスのポートへ LAN 越しに直接話す。入出力のファイルは他の PC 側に置く。使い方は `tools/localai-mcp/README.md` を Read する。
- 起動で他の GPU サービスが止まるとき・Ollama のモデルが降ろされるときは、`stop_others=true` が無い限り起動しない（他人の作業を落とさないため）。
- `claude mcp add` の `-e` は値をいくつでも取る。名前を先に置く（`claude mcp add -s user localai -e K=V -- cmd`）。`-e` の後ろに名前を置くと
  `Invalid environment variable format: <名前>` で失敗する。`codex mcp add localai --env K=V -- cmd` は通る（codex-cli 0.169.0）。
- 実機で確かめたのは、サーバー自身から LAN IP 経由での状態取得、Open WebUI の起動（running まで 15.7 秒）と停止、Stable Audio の REST と Gradio 5.33 での生成、
  起動の拒否、`claude mcp get` の Connected、`claude -p` からの `localai_status` の呼び出し。Gradio 6.17（Pixal3D・Qwen3-TTS）、Hunyuan3D・Strata・ComfyUI・AudioCraft の
  実機での呼び出しと、他の PC からの到達は確かめていない。
