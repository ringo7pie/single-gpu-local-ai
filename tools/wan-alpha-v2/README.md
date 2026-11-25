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

## ライセンス

Wan-AI/Wan2.1-T2V-14B は Apache-2.0（LICENSE.txt）、htdong/Wan-Alpha-v2.0 は Apache-2.0（モデルのページの License の欄。LICENSE のファイルは無い）、
WeChatCV/Wan-Alpha のコードは MIT（LICENSE、Copyright (c) 2026 WeChat Vision）。
