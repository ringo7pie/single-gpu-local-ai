#!/usr/bin/env python3
"""Wan-Alpha v2.0 の公式の推論の台本（vendor/Wan-Alpha/generate_dora_lightx2v_mask.py）を、A30（24 GB）1 枚で動かす起動台本。

上流のファイルは書き換えず、import した後に次の 3 つを差し替えてから、上流の台本を __main__ として実行する。

1. 土台の DiT（Wan2.1-T2V-14B、fp32 で 57.2 GB）を bf16 で読む（28.6 GB）。
2. DoRA（t2v.safetensors）と LightX2V の LoRA を、1 つの重みずつその場で合成する。上流は state_dict を丸ごと複製し（DoRA）、
   元の重みの控えを CPU に取る（LightX2V）ので、RAM 62.5 GiB の機体では足りない。合成の計算は fp32 で行い、bf16 に戻す。
3. 合成した後、transformer ブロックの中の nn.Linear を INT8 の重みだけの量子化にする（出力チャネルごとの対称 scale、
   計算は bf16 に戻して行う。A30 には FP8 の演算器が無いため INT8 にした）。DiT は約 14 GB になり、24 GB に載る。
   ブロックの外（埋め込み・ヘッド）と正規化・modulation は、上流と同じ fp32 に戻す。

引数は上流の台本と同じ。1 GPU なので --t5_cpu（T5 を CPU で回す）を付けること。例は README.md。
"""

from __future__ import annotations

import logging
import os
import runpy
import sys
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F
from safetensors import safe_open

HERE = Path(__file__).resolve().parent
UPSTREAM = HERE / "vendor" / "Wan-Alpha"
SCRIPT = UPSTREAM / "generate_dora_lightx2v_mask.py"
sys.path.insert(0, str(UPSTREAM))

from wan.modules import lightx2v_lora_adapter, model_dora_lightx2v  # noqa: E402

WanModel = model_dora_lightx2v.WanModel_dora_lightx2v


# 1 なら INT8 の重みを pinned の CPU メモリに置き、Linear ごとに GPU へ送る（81 コマでは DiT 14 GB と作業領域が 24 GB に収まらない）。
STREAM_WEIGHTS = os.environ.get("WAN_INT8_STREAM", "1") == "1"


class Int8Linear(nn.Module):
    """重みを int8（出力チャネルごとの scale）で持ち、forward のたびに入力の型へ戻して F.linear する。

    STREAM_WEIGHTS のときは int8 の重みを buffer にせず（model.to(device) で GPU へ移らないように）pinned の CPU に置き、
    forward のたびに GPU へ送る。scale と bias は小さいので buffer のまま GPU に置く。
    """

    def __init__(self, linear: nn.Linear, device: torch.device) -> None:
        super().__init__()
        self.in_features = linear.in_features
        self.out_features = linear.out_features
        weight = linear.weight.detach().to(device=device, dtype=torch.float32)
        scale = weight.abs().amax(dim=1, keepdim=True).clamp(min=1e-8) / 127.0
        quantized = torch.round(weight / scale).clamp(-127, 127).to(torch.int8)
        del weight
        if STREAM_WEIGHTS:
            self.weight_int8_cpu = quantized.cpu().pin_memory()
            self.weight_int8 = None
        else:
            self.register_buffer("weight_int8", quantized)
        self.register_buffer("weight_scale", scale.to(torch.bfloat16))
        bias = linear.bias.detach().to(device=device, dtype=torch.bfloat16) if linear.bias is not None else None
        self.register_buffer("bias", bias)

    def int8_numel(self) -> int:
        return (self.weight_int8_cpu if self.weight_int8 is None else self.weight_int8).numel()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        compute_dtype = torch.bfloat16 if x.dtype == torch.float32 and torch.is_autocast_enabled() else x.dtype
        weight_int8 = self.weight_int8 if self.weight_int8 is not None else self.weight_int8_cpu.to(x.device, non_blocking=True)
        weight = weight_int8.to(compute_dtype) * self.weight_scale.to(compute_dtype)
        del weight_int8
        bias = self.bias.to(compute_dtype) if self.bias is not None else None
        return F.linear(x.to(compute_dtype), weight, bias)


def quantize_blocks(model: nn.Module, device: torch.device) -> tuple[int, int]:
    """model.blocks の中の nn.Linear を Int8Linear に置き換える。戻り値は (置き換えた数, int8 の重みのバイト数)。"""
    replaced = 0
    int8_bytes = 0
    for block in model.blocks:
        for parent in list(block.modules()):
            for child_name, child in list(parent.named_children()):
                if isinstance(child, nn.Linear):
                    quantized = Int8Linear(child, device)
                    setattr(parent, child_name, quantized)
                    int8_bytes += quantized.int8_numel()
                    replaced += 1
                    del child
    return replaced, int8_bytes


_original_from_pretrained = WanModel.from_pretrained.__func__


def from_pretrained_bf16(cls, *args, **kwargs):
    kwargs.setdefault("torch_dtype", torch.bfloat16)
    kwargs.setdefault("low_cpu_mem_usage", True)
    return _original_from_pretrained(cls, *args, **kwargs)


@torch.no_grad()
def load_dora_inplace(self, dora_path: str, alpha: float) -> None:
    """上流の load_dora と同じ式（W' = m / ||W + αBA|| · (W + αBA)、行ごとのノルム）を、1 つの重みずつその場で当てる。"""
    params = dict(self.named_parameters())
    applied = 0
    with safe_open(dora_path, framework="pt") as handle:
        keys = list(handle.keys())
        for key in keys:
            if ".lora_B." not in key:
                continue
            parts = key.split(".")
            index = parts.index("lora_B")
            if len(parts) > index + 2:
                parts.pop(index + 1)
            parts.pop(index)
            if parts[0] == "diffusion_model":
                parts.pop(0)
            target = ".".join(parts)
            weight_b = handle.get_tensor(key).float()
            weight_a = handle.get_tensor(key.replace(".lora_B.", ".lora_A.")).float()
            magnitude_key = key.replace(".lora_B.", ".lora_magnitude_vector.").replace("lora_B", "lora_magnitude_vector")
            magnitude = handle.get_tensor(magnitude_key).float()
            param = params[target]
            if weight_b.dim() == 4:
                delta = (alpha * torch.mm(weight_b.squeeze(3).squeeze(2), weight_a.squeeze(3).squeeze(2))).unsqueeze(2).unsqueeze(3)
                patched = param.float() + delta
                norm = torch.linalg.norm(patched, dim=tuple(range(1, patched.dim())), keepdim=True)
            else:
                patched = param.float() + alpha * torch.mm(weight_b, weight_a)
                norm = torch.linalg.norm(patched, dim=1, keepdim=True)
            norm = torch.clamp(norm, min=1e-8)
            param.copy_(((magnitude.view_as(norm) / norm) * patched).to(param.dtype))
            applied += 1
    logging.info(f"DoRA をその場で当てました: {applied} 個の重み")


@torch.no_grad()
def load_lightx2v_inplace_and_quantize(self, lora_path: str) -> None:
    """LightX2V の LoRA（lora_A/B・lora_down/up・diff・diff_b・diff_m）を控えを取らずに当て、その後ブロックを INT8 にする。"""
    params = dict(self.named_parameters())
    params.update(dict(self.named_buffers()))
    applied = 0
    with safe_open(lora_path, framework="pt") as handle:
        keys = set(handle.keys())
        for key in sorted(keys):
            name = key[len("diffusion_model."):] if key.startswith("diffusion_model.") else key
            for suffix_a, suffix_b in (("lora_A.weight", "lora_B.weight"), ("lora_down.weight", "lora_up.weight")):
                if name.endswith(suffix_a) and key.replace(suffix_a, suffix_b) in keys:
                    target = name.replace(suffix_a, "weight")
                    if target in params:
                        param = params[target]
                        lora_a = handle.get_tensor(key).float()
                        lora_b = handle.get_tensor(key.replace(suffix_a, suffix_b)).float()
                        if param.shape == (lora_b.shape[0], lora_a.shape[1]):
                            param.copy_((param.float() + lora_b @ lora_a).to(param.dtype))
                            applied += 1
            for suffix, target_suffix in (("diff", "weight"), ("diff_b", "bias"), ("diff_m", "modulation")):
                if name.endswith("." + suffix):
                    target = name[: -len(suffix)] + target_suffix
                    if target in params and tuple(params[target].shape) == tuple(handle.get_slice(key).get_shape()):
                        param = params[target]
                        param.copy_((param.float() + handle.get_tensor(key).float()).to(param.dtype))
                        applied += 1
    assert applied > 0, "LightX2V の LoRA が 1 つも当たらなかった"
    logging.info(f"LightX2V の LoRA をその場で当てました: {applied} 個")
    device = torch.device("cuda", torch.cuda.current_device())
    replaced, int8_bytes = quantize_blocks(self, device)
    # 埋め込み・ヘッド・正規化・modulation は上流と同じ fp32 に戻す（上流の重みは fp32 で、autocast(float32) の箇所がある）。
    # int8 の buffer は float() で変わらない。
    self.float()
    torch.cuda.empty_cache()
    logging.info(
        f"INT8 にしました: Linear {replaced} 個、int8 の重み {int8_bytes / 1024 ** 3:.2f} GiB"
        f"（{'pinned の CPU に置き、使うたびに GPU へ送る' if STREAM_WEIGHTS else 'GPU に置く'}）"
        f"（GPU の使用 {torch.cuda.memory_allocated() / 1024 ** 3:.2f} GiB）"
    )


WanModel.from_pretrained = classmethod(from_pretrained_bf16)
WanModel.load_dora = load_dora_inplace
WanModel.load_lightx2v_lora = load_lightx2v_inplace_and_quantize
# 念のため、上流の LoRA 当て（控えを取る版）が呼ばれたら止める。
lightx2v_lora_adapter.WanLoraWrapper.apply_lora = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("上流の apply_lora は使わない"))

if __name__ == "__main__":
    sys.argv = [str(SCRIPT), *sys.argv[1:]]
    runpy.run_path(str(SCRIPT), run_name="__main__")
