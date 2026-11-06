# video-models

動画生成の 3 モデル（HunyuanVideo 1.5 / Kandinsky 6.0 Lite / LTX 2.5 Fast）を、既存の ComfyUI（`tools/comfyui`、8188）の
ワークフローとして導入し、入口ページ（8080）の ComfyUI のカードから使い方を開けるようにする。
計画: `~/.claude/plans/zesty-stirring-pike.md`。

## 完了条件

- `./localai test` が全部 OK。`run-comfyui doctor --skip-selftest` が追加ノード・パッチ・動画の重みを含めて OK。
- 実機で 8 本のワークフロー（Hunyuan T2V/I2V、LTX T2V/I2V、Kandinsky Lite/Lite distill × T2V/I2V）が完走し mp4 ができる。
  時間・VRAM ピーク・memory.peak を記録する。
- 入口ページの ComfyUI カードに動画生成の「使い方」が出て、`/guides/video-generation.html` が開く。
- README・CLAUDE.md・ナレッジ（areas/comfyui.md・resources/comfyui-measurements.md・areas/web-services.md）に反映。

## 経緯

- 2026-11-05 開始。GPU は `~/Proj/USDFormat` の Isaac Lab（VRAM 5.6 GiB、使用率 89%）が使用中。GPU を使う工程は
  `tools/comfyui/scripts/wait-gpu-idle.sh` が GPU_IDLE を出してから行う。

## 結果（2026-11-06 14:50 時点、完了）

- 2026-11-06 11:33 に USDFormat の学習が終わり GPU が空いた。待機スクリプトは `--allow ai_finance` が nvidia-smi の process_name（`python`）に
  当たらず終わらない不具合があったので、コマンドラインでも照合するよう直した（試験を追加）。
- 実機で 8 本すべて完走し mp4 ができた（LTX・Kandinsky は音声付き、無音ではない）。短い設定 3 本と、HunyuanVideo の 1080p 後段も 2 回（EasyCache あり・なし）通した。
  実測は `tools/comfyui/README.md`・`tools/portal/guides/video-generation.html`・写しの日本語ノート・`docs/hardware-requirements.md`・
  `resources/comfyui-measurements.md`・`resources/comfyui-video.md` に書いた。
- Kandinsky の日本語ノートがプロンプトのノードを上流の題名で指していた食い違いを直した（題名を `config.KANDINSKY6_PROMPT_TITLE` にまとめ、試験を追加）。
- `./localai test` は OK 11 / FAIL 0 / SKIP 3。`run-comfyui doctor --skip-selftest` は 79 行 OK（NG は `--skip-selftest` で自己試験を省いた 1 行だけ）。
  入口ページ（他の PC から見える LAN のアドレスの 8080）からガイドが開き、カードに 2 本目のリンクがある。
- ComfyUI-GGUF の当て物は要らなかった（計画の 2 本のパッチは作っていない）。
- 確かめていないこと: 画面（ComfyUI の UI と入口ページ）の見え方は headless の Brave が終わらず写せていない。1080p 後段の格子状の模様の原因。
