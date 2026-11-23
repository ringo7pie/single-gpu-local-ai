"""実機（A30）で、Stable Audio 3.0 Medium の 3 つのモード（文章 → 音・音 → 音・続き）の wav ができるまでを確かめる試験。

GPU と重みを実際に使い、数分かかる。stable-audio.service や、ほかの GPU サービス・Qwen が VRAM を使っていると
足りないので、先に止めること。`generate` はモデルを 1 件ごとに読み込み直す（常駐しない）。

実行:
    systemctl --user stop stable-audio
    tools/stable-audio/.venv/bin/python tools/stable-audio/tests/test_generate_real.py

元の音は、1 件目（文章 → 音）で作った wav を使う（音 → 音で作り変え、その先頭 5 秒の続きを作る）。
測るもの: モードごとの所要時間（読み込み込み）と生成時間、nvidia-smi で見た VRAM のピーク、wav の長さ・サンプルレート・チャンネル数。
出力先: tools/stable-audio/outputs/smoke/
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

from saudio import config, params  # noqa: E402

OUTPUT_DIR = config.OUTPUTS_DIR / "smoke"
PROMPT = "Lo-fi hip hop beat with mellow Rhodes chords and soft boom bap drums, 84 BPM"
AUDIO_PROMPT = "Bossa nova guitar and brushed drums, warm and relaxed"
CONTINUE_PROMPT = "Lo-fi hip hop beat continues with a gentle saxophone melody"
DURATION_TOLERANCE_SEC = 0.05


class VramSampler(threading.Thread):
    """nvidia-smi で GPU 全体の使用量を 0.5 秒おきに見て、最大値を覚える。"""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self._stop_event = threading.Event()
        self.peak_mib = 0

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                output = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5,
                ).stdout.strip().splitlines()
                if output:
                    self.peak_mib = max(self.peak_mib, int(output[0]))
            except (OSError, subprocess.SubprocessError, ValueError):
                pass
            self._stop_event.wait(0.5)

    def stop(self) -> int:
        self._stop_event.set()
        self.join(timeout=5)
        return self.peak_mib


def run_generate(name: str, seconds: float, extra: list[str], prompt: str) -> dict:
    out_path = OUTPUT_DIR / f"{name}.wav"
    command = [
        str(TOOL_DIR / "run-stable-audio"), "generate", "--prompt", prompt, "--duration", str(seconds),
        "--seed", "1234", "--out", str(out_path), *extra,
    ]
    sampler = VramSampler()
    sampler.start()
    started = time.monotonic()
    completed = subprocess.run(command, capture_output=True, text=True, env=dict(os.environ))
    elapsed = time.monotonic() - started
    nvidia_peak = sampler.stop()
    if completed.returncode != 0:
        print(completed.stdout)
        print(completed.stderr, file=sys.stderr)
        raise SystemExit(f"{name} の生成に失敗しました（終了コード {completed.returncode}）")

    import soundfile

    info = soundfile.info(str(out_path))
    metadata = json.loads(out_path.with_suffix(".json").read_text(encoding="utf-8"))
    actual_seconds = info.frames / info.samplerate
    assert info.samplerate == params.SAMPLE_RATE, (name, info.samplerate)
    assert info.channels == params.CHANNELS, (name, info.channels)
    assert abs(actual_seconds - seconds) <= DURATION_TOLERANCE_SEC, (name, actual_seconds, seconds)
    data, _ = soundfile.read(str(out_path), dtype="float32")
    peak = float(abs(data).max())
    assert peak > 0.01, (name, "ほぼ無音", peak)
    return {
        "name": name,
        "mode": metadata["mode"],
        "seconds": round(actual_seconds, 2),
        "peak_amplitude": round(peak, 3),
        "total_sec_including_load": round(elapsed, 1),
        "generate_sec": metadata["elapsed_sec"],
        "torch_peak_vram_mib": metadata["peak_vram_mib"],
        "nvidia_smi_peak_mib": nvidia_peak,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=float, default=30.0, help="文章 → 音・音 → 音の長さ（続きは元の音 5 秒 + この長さの半分）")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results = [run_generate("text", args.seconds, [], PROMPT)]
    source = OUTPUT_DIR / "text.wav"
    results.append(
        run_generate("audio", args.seconds, ["--mode", "audio", "--source", str(source), "--init-noise-level", "0.7"], AUDIO_PROMPT)
    )
    # 続き: 元の音の先頭 5 秒を切り出して、その後ろを作る。
    import soundfile

    data, rate = soundfile.read(str(source), dtype="float32")
    head = OUTPUT_DIR / "head5.wav"
    soundfile.write(str(head), data[: int(rate * 5)], rate, subtype="PCM_16")
    continue_seconds = 5 + args.seconds / 2
    results.append(
        run_generate("continue", continue_seconds, ["--mode", "inpaint", "--source", str(head), "--inpaint-start", "5"], CONTINUE_PROMPT)
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print("合格: 3 つのモードとも、wav の長さ・サンプルレート・チャンネル数が指定どおりで、無音ではありません。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
