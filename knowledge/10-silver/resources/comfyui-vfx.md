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
