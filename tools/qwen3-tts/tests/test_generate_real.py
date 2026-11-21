"""Qwen3-TTS VoiceDesign の実機試験（GPU と重みを実際に使う。1〜数分）。

先に systemctl --user stop qwen3-tts（画面と VRAM を取り合わないように）。
実行: tools/qwen3-tts/.venv/bin/python tools/qwen3-tts/tests/test_generate_real.py [--device cuda:0|cpu]

日本語の文と、声の特徴の説明（instruct）から 1 本作り、outputs/ に wav を書く。
読み込み時間・生成時間・音声の長さ・VRAM のピーク（torch の割り当て）を表示する。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

from qtts import config  # noqa: E402

os.environ.update(config.runtime_env_overrides())

TEXT = "こんにちは。今日は良い天気ですね。この声は、文章で指定した特徴から作られています。"
INSTRUCT = "落ち着いた低めの声の三十代の男性。ゆっくり、はっきりと話す。"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--dtype", default=config.DEFAULT_DTYPE, choices=config.ALLOWED_DTYPES)
    args = parser.parse_args()

    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    on_gpu = args.device.startswith("cuda")
    if not on_gpu:
        os.environ["CUDA_VISIBLE_DEVICES"] = ""

    started = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(
        str(config.MODEL_DIR), device_map=args.device, dtype=getattr(torch, args.dtype)
    )
    load_sec = time.perf_counter() - started
    if on_gpu:
        torch.cuda.reset_peak_memory_stats()

    started = time.perf_counter()
    wavs, sample_rate = model.generate_voice_design(text=TEXT, language="Japanese", instruct=INSTRUCT)
    gen_sec = time.perf_counter() - started

    wav = np.asarray(wavs[0], dtype=np.float32)
    seconds = wav.shape[0] / float(sample_rate)
    rms = float(np.sqrt(np.mean(np.square(wav)))) if wav.size else 0.0
    config.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.OUTPUTS_DIR / f"test-real-{time.strftime('%Y%m%d-%H%M%S')}.wav"
    sf.write(str(out_path), wav, sample_rate)

    print(f"読み込み {load_sec:.1f} 秒、生成 {gen_sec:.1f} 秒、音声 {seconds:.2f} 秒（{sample_rate} Hz）、RMS {rms:.4f}")
    if on_gpu:
        print(f"VRAM ピーク（torch の割り当て）{torch.cuda.max_memory_allocated() / 2**20:.0f} MiB")
    print(f"出力: {out_path}")

    failures = []
    if not 1.0 <= seconds <= 60.0:
        failures.append(f"音声の長さが想定外: {seconds:.2f} 秒")
    if rms < 1e-3:
        failures.append(f"ほぼ無音: RMS {rms:.5f}")
    for failure in failures:
        print(f"NG: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
