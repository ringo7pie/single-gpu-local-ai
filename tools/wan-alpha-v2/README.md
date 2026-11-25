# wan-alpha-v2

Wan-Alpha v2.0（[htdong/Wan-Alpha-v2.0](https://huggingface.co/htdong/Wan-Alpha-v2.0)、透明度つきの動画）を、公式の推論の台本
（[WeChatCV/Wan-Alpha](https://github.com/WeChatCV/Wan-Alpha) の `generate_dora_lightx2v_mask.py`）で A30（24 GB）1 枚で動かすための試し。
ComfyUI 版は未公開（2026-11-25 時点でモデルのページは「Coming soon」）なので、ComfyUI（`tools/comfyui`）とは別の venv に入れる。
ComfyUI の環境には触れない（LightX2V の LoRA だけ、ComfyUI に入れたファイルを読む）。サービスにはしていない。

## 量子化して動かす理由

土台の Wan-AI/Wan2.1-T2V-14B の DiT は fp32 で 57.2 GB、bf16 でも 28.6 GB あり、24 GB に載らない。上流の台本は 8 GPU の
torchrun（FSDP）前提で、1 GPU では DiT を丸ごと GPU に移す（`self.model.to(self.device)`）。そこで `run_int8.py` が、上流のファイルを
書き換えずに次を差し替えてから上流の台本を実行する。

1. DiT を bf16 で読む。
2. DoRA（`t2v.safetensors`）と LightX2V の LoRA を、1 つの重みずつその場で合成する（上流は state_dict を複製し、LightX2V では元の重みの
   控えを CPU に取るので、RAM 62.5 GiB では足りない）。合成の計算は fp32。
3. transformer ブロックの中の nn.Linear を INT8 の重みだけの量子化にする（出力チャネルごとの対称 scale。計算は bf16 に戻す。
   A30 には FP8 の演算器が無いので INT8）。ブロックの外と正規化・modulation は上流と同じ fp32。

T5 は `--t5_cpu` で CPU に置く（GPU に置くと DiT と合わせて 24 GB を超える）。flash_attn は入れず、PyTorch の SDPA を使う
（上流の `wan/modules/attention.py` が flash_attn が無ければ SDPA に切り替える）。xfuser・dashscope・gradio は 1 GPU の推論では読まれないので入れない。

## 導入

2026-11-25 の試しの後、重み（`models/`、66 GB）・`.venv`・`vendor/` は消した（ディスクの空きのため）。試しの出力（`output/`）とログは残している。
もう一度動かすときは、下の手順で取り直す。

```bash
cd tools/wan-alpha-v2
git clone https://github.com/WeChatCV/Wan-Alpha.git vendor/Wan-Alpha
git -C vendor/Wan-Alpha checkout 9e5d0533da2dea390bbb0e50c59adfebe5613323
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cu126
uv pip install --python .venv/bin/python -r requirements.lock.txt
./fetch-models.sh        # 約 69 GB。sha256 を照合し、合わなければ消して失敗にする
```

## 1 本作る

GPU を使うほかのサービス（ComfyUI など）を止めてから:

```bash
./run-trial.sh v2_832x480_f81 '832*480' 81     # 出力は output/<タグ>/（checkerboard.mp4 と RGBA の PNG の pngs.zip）、trial.json に秒数と VRAM
WAN_ALPHA_PROMPT="..." ./run-trial.sh <タグ>   # プロンプトを変える
```

## 実機試験の結果（2026-11-25、ComfyUI を止めて GPU を空けた状態。`run-trial.sh v2_832x480_f81 '832*480' 81`）

プロンプト「a swirling cloud of glowing violet magic mist, soft wisps, centered, no background」、832x480・81 コマ・LightX2V 4 ステップ・cfg 1.0・seed 42。

| 段 | 時刻の差 |
|---|---|
| T5・VAE の読み込み〜DiT の読み込み・DoRA / LightX2V の合成・INT8 化 | 約 3 分（15:52:59 → 15:56:02） |
| T5（CPU）で文章を埋め込む | 約 3 分 20 秒（〜15:59:18） |
| サンプリング 4 ステップ | 84 秒（20.9 秒/ステップ） |
| VAE で戻して保存 | 約 30 秒 |
| 合計 | 503.7 秒 |

- VRAM の最大は 11859 MiB（nvidia-smi の機体全体）。INT8 の重み（13.09 GiB）は pinned の CPU に置いて使うたびに送る（`WAN_INT8_STREAM=1`、既定）。
  GPU に置いたまま（`WAN_INT8_STREAM=0`）では、81 コマで 1 ステップ目に VRAM が足りなくなった（PyTorch の確保 20.34 GiB）。
- RAM は読み込み中に 36 GiB、swap は 14 GiB まで使った（DiT の bf16 28.6 GB と T5 11 GB を CPU に持つため）。
- 出力は `output/<タグ>/<時刻>/checkerboard.mp4`（市松の背景に重ねた確認用の動画）と `pngs.zip`（RGBA の PNG 81 枚、832x480）。
  81 枚の平均で 75% の画素のアルファが 0、縁の 99.7% が 0（四隅に 5 以下が残るコマがある）。
- 同じプロンプトで v1.0（ComfyUI の Wan 2.1 Alpha）は光る渦になったが、v2.0 は中央にまとまった不透明に近い青い雲になった
  （上流の台本は gauss_mask で中央の楕円に寄せる）。どちらが良いかは用途で決めること。
- 上流の requirements.txt に無い `av` を足した。上流のモデルは `flash_attention()` を直接呼ぶので flash-attn（2.8.3 の公式 wheel）が要る。

## ライセンス

Wan-AI/Wan2.1-T2V-14B は Apache-2.0（LICENSE.txt）、htdong/Wan-Alpha-v2.0 は Apache-2.0（モデルのページの License の欄。LICENSE のファイルは無い）、
WeChatCV/Wan-Alpha のコードは MIT（LICENSE、Copyright (c) 2026 WeChat Vision）。
