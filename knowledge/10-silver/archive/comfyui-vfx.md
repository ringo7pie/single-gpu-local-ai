# comfyui-vfx

目的: 別の PC（Windows）の Claude Code が ComfyUI の HTTP API だけでゲームのエフェクトを作れるようにする。

完了条件:
- [x] Wan 2.1 Alpha と LayerDiffuse の重み 10 ファイルを sha256 で照合して入れる
- [x] 試しの生成 2 本（出力は `output/dq_vfx_trial/`）と、`workflows/VFX/` に UI 形式・API 形式の 4 ファイル
- [x] 入口ページ（8080）の `GET /api` の `comfyui`・使い方ページ `guides/vfx.html`・localai-mcp の `workflow_name`
- [x] 任意の Wan-Alpha v2.0（別の venv、`tools/wan-alpha-v2`）: INT8 にして 1 本作った（504 秒・VRAM 11859 MiB）

上流の事情と実測は `knowledge/10-silver/resources/comfyui-vfx.md` を Read する。
