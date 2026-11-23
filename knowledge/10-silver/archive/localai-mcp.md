# localai-mcp

他の PC で動く stdio の MCP サーバー（`tools/localai-mcp/localai_mcp.py`、標準ライブラリだけ）を作り、Claude Code / Codex から
入口ページ（8080）の全サービスの起動・停止と、各 AI の API（Strata・AudioCraft・Stable Audio・Qwen3-TTS・Hunyuan3D・Pixal3D・ComfyUI）を使えるようにする。
計画: `~/.claude/plans/pc-claude-code-codex-goofy-valiant-of-a64c90e0-4bc5-4980-a9b9-ee1a88007c57.md`。

## 完了条件

- `uvx --with pytest pytest tools/localai-mcp/tests -q` と `./localai test` が OK。
- サーバー上で `LOCALAI_SERVER=<LAN IP>` の MCP から、状態・起動停止（GPU を使わないもの）・少なくとも 1 つの生成が通る。
- 導入スクリプト（sh / ps1）と README、CLAUDE.md、ナレッジ（lan-access・portal-control・local-ai-tools）に反映。
- ufw の開放コマンドをユーザーに提示した（実施はユーザー）。

## 経緯

- 2026-11-23 開始。方式はユーザーが選んだ「他 PC 側 MCP + ufw 開放」。対象 OS は Windows・Linux・macOS。
- 開始時、Stable Audio（7864）が稼働中で 14:29 に生成が来ていた。実機確認でこれを止めない。
