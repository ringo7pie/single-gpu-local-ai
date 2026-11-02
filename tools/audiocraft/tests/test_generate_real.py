"""実機（A30）で、文章から作曲・効果音の wav ができるまでを確かめる試験。モック試験とは別。

GPU と重みを実際に使い、数分かかる。AudioCraft のサービス（audiocraft.service）や、ほかの GPU サービス・Qwen が
VRAM を使っていると足りないので、先に止めること。モデルの読み込みを 1 件ごとにやり直す（`generate` は常駐しない）。

実行:
    systemctl --user stop audiocraft
    tools/audiocraft/.venv/bin/python tools/audiocraft/tests/test_generate_real.py
    tools/audiocraft/.venv/bin/python tools/audiocraft/tests/test_generate_real.py --device cpu --music-seconds 2 --sound-seconds 2

測るもの: 種類ごとの所要時間（読み込み込み）と、nvidia-smi で見た VRAM のピーク、wav の長さ・サンプルレート。
出力先: tools/audiocraft/outputs/smoke/
--device cpu のときは GPU を一切使わない（CUDA_VISIBLE_DEVICES を空にして流す）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

from acraft import config, params  # noqa: E402

OUTPUT_DIR = config.OUTPUTS_DIR / "smoke"
PROMPTS = {
    params.KIND_MUSIC: "calm lo-fi piano melody with soft drums, relaxing, 80 bpm",
    params.KIND_SOUND: "footsteps on gravel, slow walking pace",
}
# 長さの許容誤差。音声圧縮モデルのフレーム単位（50 Hz）で丸められるため、わずかにずれる。
DURATION_TOLERANCE_SEC = 0.1


class VramSampler(threading.Thread):
    """nvidia-smi を 1 秒おきに読み、使用中 VRAM（MiB）の最大値を覚える。"""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.peak_mib = 0
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                completed = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=10, check=True,
                )
                self.peak_mib = max(self.peak_mib, int(completed.stdout.strip().splitlines()[0]))
            except (subprocess.SubprocessError, OSError, ValueError):
                pass
            self._stop_event.wait(1.0)

    def stop(self) -> int:
        self._stop_event.set()
        self.join(timeout=5)
        return self.peak_mib


def run_generate(kind: str, seconds: float, device: str) -> dict:
    out_path = OUTPUT_DIR / f"{kind}.wav"
    command = [
        str(TOOL_DIR / "run-audiocraft"), "generate",
        "--kind", kind, "--prompt", PROMPTS[kind], "--duration", str(seconds),
        "--seed", "1234", "--device", device, "--out", str(out_path),
    ]
    sampler = VramSampler() if device != "cpu" else None
    if sampler is not None:
        sampler.start()
    started = time.monotonic()
    completed = subprocess.run(command, capture_output=True, text=True, env=dict(os.environ))
    elapsed = time.monotonic() - started
    nvidia_peak = sampler.stop() if sampler is not None else None
    if completed.returncode != 0:
        print(completed.stdout)
        print(completed.stderr, file=sys.stderr)
        raise SystemExit(f"{kind} の生成に失敗しました（終了コード {completed.returncode}）")

    import soundfile

    info = soundfile.info(str(out_path))
    spec = params.KIND_SPECS[kind]
    metadata = json.loads(out_path.with_suffix(".json").read_text(encoding="utf-8"))
    actual_seconds = info.frames / info.samplerate
    assert info.samplerate == spec.sample_rate, (kind, info.samplerate, spec.sample_rate)
    assert abs(actual_seconds - seconds) <= DURATION_TOLERANCE_SEC, (kind, actual_seconds, seconds)
    assert info.channels == 1, (kind, info.channels)
    return {
        "kind": kind,
        "wav": str(out_path),
        "sample_rate": info.samplerate,
        "seconds": round(actual_seconds, 2),
        "total_sec_including_load": round(elapsed, 1),
        "generate_sec": metadata["elapsed_sec"],
        "torch_peak_vram_mib": metadata["peak_vram_mib"],
        "nvidia_smi_peak_mib": nvidia_peak,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", default="auto", choices=("auto", "cuda", "cpu"))
    parser.add_argument("--music-seconds", type=float, default=10.0)
    parser.add_argument("--sound-seconds", type=float, default=5.0)
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results = [
        run_generate(params.KIND_MUSIC, args.music_seconds, args.device),
        run_generate(params.KIND_SOUND, args.sound_seconds, args.device),
    ]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print("合格: wav の長さ・サンプルレート・チャンネル数が指定どおりです。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
