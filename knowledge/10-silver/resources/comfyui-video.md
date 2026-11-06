# comfyui-video

ComfyUI（`tools/comfyui`）の動画生成 3 モデル（HunyuanVideo 1.5 / Kandinsky 6.0 Lite / LTX 2.5 Fast）の上流の事情と、
A30 に載せるために選んだこと（2026-11-05）。導入をやり直すときと、上流や重みを替えるときに Read する。
毎回の運用で効く事実は `knowledge/10-silver/areas/comfyui.md` にある。

## 本体と拡張

- ComfyUI v0.41.1 は HunyuanVideo 1.5 と LTX 2.5 を本体のノードで扱う。同梱テンプレート `video_hunyuan_video_1.5_720p_{t2v,i2v}` と
  `video_ltx2_5_{t2v,i2v}` は venv の `comfyui_workflow_templates_json/templates/` にある。LTX 2.5 のテンプレートは subgraph を 1 つ使う。
- Kandinsky 6.0 は本体に無い（本体への PR #16825 は 2026-11-05 時点で未マージ）。公式拡張 `kandinskylab/kandinsky-6` の `comfyui/`
  （v1.0.2、コミット `7ceeefad`）を `vendor/kandinsky-6` に置き、`custom_nodes/kandinsky6` からシンボリックリンクで指す。
  拡張は `comfy.model_detection.detect_unet_config` を包んで Kandinsky 6 を見分ける。音声 VAE の置き場は `models/audio_vae`。
- 拡張の MagCache は Pro 専用で、Lite に付けると `model_dim` の違いで止まる。distill は `Kandinsky6Sampler` の CFG 1・denoise 1 でないと止まる。
- 超解像（`Kandinsky6VSRUpscale` など）は別の拡張 `kandinsky6-sr` のノードで、入れていない。
- `city96/ComfyUI-GGUF` の最新は `6ea2651e`（2026-01-12）。issue #488（ComfyUI 0.39 で `Linear` に `input_act` が渡る）は v0.41.1 では起きない
  （`comfy/ops.py` の `linear_input_act` は INT8 の重み以外で `linear(x)` を呼ぶ）ので、当て物をしていない。

## 重み（どれも gated ではない。この機体に HF のトークンは無い）

- Hugging Face の `Comfy-Org/HunyuanVideo_1.5_repackaged` は `Comfy-Org/Hunyuan-Video-1.5` へ、`Comfy-Org/HunyuanVideo_repackaged` は
  `Comfy-Org/Hunyuan-Video` へ改名されている。旧名はリダイレクトするので、tree API は `curl -L` で読む。
- LTX 2.5 Fast は LTX 2.5 の distilled 22B。公式 `Lightricks/LTX-2.5` は gated で、int8 でも 21.5 GB。ungated の `unsloth/LTX-2.5-GGUF` に
  distilled の GGUF・文字エンコーダ bf16（26.3 GB）・畳み込み VAE・音声 VAE・空間アップスケーラが揃う。
- unsloth の UD-Q4_K_XL の GGUF は `general.architecture=ltxv`（ComfyUI-GGUF が受け付ける名前）。ComfyUI-GGUF issue #477 で問題になる
  3 つのテンソル（`*_embeddings_connector.learnable_registers`・`keyframes_abs_pos_embedding`）は、このファイルでは F32。
  GGUF のヘッダは先頭 32 MiB の範囲取得で読める（2026-11-05 に確認）。
- 取得する 21 ファイルと sha256 は `tools/comfyui/cfui/config.py` の `VIDEO_MODEL_FILES` を Read する。

## ライセンス

- HunyuanVideo 1.5 は Tencent Hunyuan Community License。対象地域から EU・英国・韓国を除き、月間 1 億人を超えるサービスは別の許諾が要る。
- LTX 2.5 は LTX-2 Community License。Kandinsky 6.0 の拡張と重みは MIT だが、音声 VAE（MMAudio の `v1-44.pth`）は CC BY-NC 4.0。

## GPU を使わない確認

- `CUDA_VISIBLE_DEVICES="" main.py --cpu --listen 127.0.0.1 --port 8189` で、ComfyUI-GGUF と kandinsky6 は読み込める。
  ComfyUI-Trellis2 だけは CUDA が無いので IMPORT FAILED になる（3D は確かめられない）。

## 実機で確かめたこと（2026-11-06）

- v0.41.1 は重みを dynamic VRAM loading で載せる。journal には `prepared for dynamic VRAM loading. N MB Staged` と出て、`loaded partially` は出ない。
  LTX の文字エンコーダ bf16（24,998 MB）も、VRAM に入らない分を RAM に置いて動く。LTX の GGUF は `loaded completely; 13618.22 MB loaded`。
- ComfyUI-GGUF（`6ea2651e`）は当て物なしで unsloth の LTX 2.5 UD-Q4_K_XL を読める（issue #488・#477 の症状は出ない）。
  Kandinsky 6 の拡張は fp8_scaled の Qwen2.5-VL 7B をそのまま受ける。
- 動画のモデルを替えて続けて流すと、comfyui の cgroup の memory.current が MemoryHigh（44 GiB）に張り付き、swap を最大 5.1 GiB 使う
  （anon は最大 22.1 GiB、残りは重みのページキャッシュ）。止まりはしない。ComfyUI を止めて起動し直すと空く。
- HunyuanVideo 1.5 の 1080p の後段（グループ「Video Upscale 1080P」の枠内を全部有効、33 コマ）は 11.3 分で 1920×1072 を出す。
  1080p の VAE デコードは `Ran out of memory when regular VAE decoding, retrying with tiled VAE decoding.` と出て、自動でタイル分割に切り替わって終わる。
  細部は増えるが、建物に格子状の模様が出る。グループ内の EasyCache を切っても模様は変わらない（11.8 分）。fp8_scaled が原因かは確かめていない。
- 実測の値は `knowledge/10-silver/resources/comfyui-measurements.md` の「動画生成」を Read する。

