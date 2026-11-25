# comfyui-vfx

ComfyUI（`tools/comfyui`、v0.41.1）のゲームのエフェクト 2 本（2026-11-25 導入）。上流の事情と A30 での実測。
VFX のワークフローを直すとき、上流や重みを替えるとき、他の PC から使う手順を答えるときに Read する。

## 構成

- Wan 2.1 Alpha T2V 14B は同梱テンプレート `video_wan2.1_alpha_t2v_14B.json` を土台にする。標準ノードだけで動き、custom node も ComfyUI の更新も要らない。
- LayerDiffuse は custom node `huchenlei/ComfyUI-layerdiffuse`（`b4f6a9e0`）と `sd_xl_base_1.0.safetensors`。SDXL の土台は base 1.0 だけにする（依頼元の台帳の都合）。
- ComfyUI-layerdiffuse の requirements を入れても、増えるのは `diffusers==0.43.0` だけで既存の版は替わらない。
- 上流のままの ComfyUI-layerdiffuse は v0.41.1 で `LayeredDiffusionDecodeRGBA` が
  `AttributeError: 'JoinImageWithAlpha' object has no attribute 'join_image_with_alpha'` で落ちる（標準ノードが V3 の classmethod `execute` になった）。
  `tools/comfyui/patches/layerdiffuse-join-alpha-v3.patch` で呼び分ける。custom node を更新したら、このパッチが要るか確かめる。
- 保存したワークフローは LAN から `http://<server>:8188/api/userdata/workflows%2FVFX%2F<名前>` で読める（実測 200）。

## 実測（A30、ComfyUI だけが GPU を使う状態）

- Wan Alpha 832x480・81 コマ・LightX2V 4 ステップ・cfg 1.0: 121 秒、VRAM 最大 21447 MiB（機体全体）、cgroup の RAM 4.6 GiB。
  出力の RGBA PNG は背景がほぼ 0 だが、四隅などに 1〜3 の画素が残る（厳密な 0 ではない）。
- LayerDiffuse 1024x1024・20 ステップ・cfg 7: 7〜10 秒、VRAM 最大 8305 MiB。縁のアルファは 0。色はシードで大きく変わる。

## Wan-Alpha v2.0（`tools/wan-alpha-v2`、ComfyUI とは別の venv）

- ComfyUI 版は未公開。公式の台本は 8 GPU 前提で、土台の DiT は fp32 57.2 GB・bf16 28.6 GB なので A30 には載らない。
  `tools/wan-alpha-v2/run_int8.py` が DiT のブロックの Linear 400 個を INT8（13.09 GiB）にし、重みを pinned の CPU から使うたびに送って動かす。
- 832x480・81 コマ・4 ステップは合計 504 秒（うち T5 を CPU で回す埋め込みが約 3 分 20 秒、サンプリング 84 秒）、VRAM 最大 11859 MiB、RAM 36 GiB と swap 14 GiB。
- 上流の DoRA と LightX2V の合成は重みを複製・控えるので RAM 62.5 GiB では足りない。`run_int8.py` はその場で合成する。

## 他の PC からの使い方（2026-11-25）

- MCP は localai-mcp 0.3.0 の `localai_vfx`（kind=wan_alpha | layerdiffuse）。入力の対応は入口ページの `GET /api` の `comfyui.workflows` から読む。
- 入口ページの `GET /remote/{localai_mcp.py,setup-remote.ps1,setup-remote.sh}` が MCP の本体と導入の台本を配る。`GET /api` の `mcp` に導入のコマンドがある。
  `.sh` を入口ページから流す導入と、LAN の住所での `localai_vfx`（両 kind）は実測で通った。Windows は PowerShell の 3 行で `.ps1` を流し、
  Claude Code の `/mcp` で localai が connected・14 ツールになった（2026-11-25、他の PC）。`install-localai-mcp.cmd` のダブルクリックは試していない。
- Windows PowerShell 5.1 で `powershell -c "irm … -OutFile …"` を入れ子で動かすと、進捗バーの描画で `out-lineoutput : インデックスが配列の境界外です`
  （IndexOutOfRangeException）と落ちた（他の PC、2026-11-25）。`$ProgressPreference = 'SilentlyContinue'` と入れ子にしない形で避ける。
  入口ページの「他の PC から使う」の欄が `install-localai-mcp.cmd`（ダブルクリックで導入・更新）を配る。
