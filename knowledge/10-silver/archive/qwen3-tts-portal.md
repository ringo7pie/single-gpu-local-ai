# qwen3-tts-portal

声の特徴を文章で指定して読み上げる Qwen3-TTS VoiceDesign（`Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`）を `tools/qwen3-tts/`
（ポート 7863、systemd user unit `qwen3-tts`）として導入し、入口ページ（tools/portal）から起動・停止・利用できるようにする。
計画: `~/.claude/plans/glowing-orbiting-bee.md`。

## 完了条件

- `./localai test` が全部 OK（新規 `qwen3-tts` を含む。既知の無関係な FAIL は区別する）。
- `run-qwen3-tts doctor` 合格。入口ページのカードから起動・停止できる。
- 実機で日本語の wav を生成し、VRAM・RAM・読み込み時間・生成時間を記録。
- ナレッジ（areas/qwen3-tts.md・web-services.md・lan-access.md）、README・NOTICE・CLAUDE.md に反映。

## 経緯

- 2026-11-21 開始。
- 2026-11-21 完了。`./localai test` は OK 12 / FAIL 0 / SKIP 4。doctor 合格、実機試験・入口ページと systemd の排他を確認。
  実測と事実は `areas/qwen3-tts.md`。残りはユーザーの操作だけ: ufw でポート 7863（と手動起動の Base の 7864）を LAN 2 セグメント分開ける（sudo）。
