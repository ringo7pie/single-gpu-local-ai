"""AudioCraft のモデルを読み込み、1 回分の生成をして wav と json に書く。

torch / audiocraft / soundfile は、関数の中で import する。こうしておくと、このモジュール自体は
torch なしでも import でき（試験で偽のモデルを差し込める）、環境変数（AUDIOCRAFT_CACHE_DIR など）を
cli 側が先に整えてから重い import が走る。

wav は soundfile で書く。上流の audio_write は ffmpeg の実行ファイルを呼ぶが、この機体には ffmpeg が無い
（sudo が使えず入れられない）ため使わない。正規化だけ上流の normalize_audio を使う。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config, params

_MIB = 1024 * 1024


@dataclass(frozen=True)
class GenerationResult:
    wav_path: Path
    meta_path: Path
    metadata: dict[str, Any]


def _log(message: str) -> None:
    print(f"[acraft] {message}", flush=True)


class Engine:
    """モデルを常駐させて生成する。生成は 1 度に 1 件（GPU が 1 枚なので Lock で直列にする）。"""

    def __init__(
        self,
        music_model_id: str,
        sound_model_id: str,
        device: str | None = None,
        outputs_dir: Path | None = None,
    ) -> None:
        self.model_ids = {params.KIND_MUSIC: music_model_id, params.KIND_SOUND: sound_model_id}
        self.requested_device = device
        self.device = device or "cpu"
        self.outputs_dir = outputs_dir if outputs_dir is not None else config.OUTPUTS_DIR
        self._models: dict[str, Any] = {}
        self._lock = threading.Lock()

    def loaded_kinds(self) -> tuple[str, ...]:
        return tuple(self._models)

    def load(self, kinds: tuple[str, ...] = params.KINDS) -> None:
        """指定した種類のモデルを読み込む（1 モデルにつき重みの読み込みに数十秒かかる）。"""

        import torch
        from audiocraft.models import AudioGen, MusicGen

        self.device = self.requested_device or ("cuda" if torch.cuda.is_available() else "cpu")
        for kind in kinds:
            model_id = self.model_ids[kind]
            started = time.monotonic()
            _log(f"{params.KIND_SPECS[kind].model_label} を読み込んでいます: {model_id}（device={self.device}）")
            if kind == params.KIND_MUSIC:
                model = MusicGen.get_pretrained(model_id, device=self.device)
            else:
                model = AudioGen.get_pretrained(model_id, device=self.device)
            self._models[kind] = model
            expected_rate = params.KIND_SPECS[kind].sample_rate
            note = "" if model.sample_rate == expected_rate else f"（想定 {expected_rate} Hz と違います）"
            _log(f"  読み込み完了 {time.monotonic() - started:.1f} 秒、sample_rate={model.sample_rate} Hz{note}")
        if self.device.startswith("cuda"):
            _log(f"  VRAM 割り当て {torch.cuda.memory_allocated() / _MIB:.0f} MiB（読み込み後）")

    def generate(
        self,
        request: params.GenerationParams,
        out_path: Path | None = None,
        now: dt.datetime | None = None,
    ) -> GenerationResult:
        """1 件を生成して wav と json に書く。読み込んでいない種類を頼むと RuntimeError。"""

        import numpy as np
        import soundfile
        import torch
        from audiocraft.data.audio_utils import normalize_audio

        model = self._models.get(request.kind)
        if model is None:
            raise RuntimeError(f"{request.kind} のモデルが読み込まれていません（読み込み済み: {self.loaded_kinds()}）")
        on_cuda = self.device.startswith("cuda")

        with self._lock:
            seed = params.resolve_seed(request.seed)
            torch.manual_seed(seed)
            if on_cuda:
                torch.cuda.manual_seed_all(seed)
                torch.cuda.reset_peak_memory_stats()
            model.set_generation_params(
                use_sampling=True,
                top_k=request.top_k,
                top_p=request.top_p,
                temperature=request.temperature,
                duration=request.duration,
                cfg_coef=request.cfg_coef,
            )
            started = time.monotonic()
            with torch.no_grad():
                wav = model.generate([request.prompt])
            if on_cuda:
                torch.cuda.synchronize()
            elapsed = time.monotonic() - started
            peak_vram_mib = torch.cuda.max_memory_allocated() / _MIB if on_cuda else None

        audio = _normalize(normalize_audio, wav[0].detach().float().cpu(), model.sample_rate)
        array = np.clip(audio.t().numpy(), -1.0, 1.0)

        created_at = now if now is not None else dt.datetime.now()
        if out_path is not None:
            wav_path = out_path if out_path.suffix == ".wav" else out_path.with_name(out_path.name + ".wav")
            meta_path = wav_path.with_suffix(".json")
        else:
            stem = params.unique_stem(self.outputs_dir, params.build_output_stem(request.kind, seed, created_at))
            wav_path, meta_path = params.output_paths(self.outputs_dir, stem)
        wav_path.parent.mkdir(parents=True, exist_ok=True)

        partial_path = wav_path.with_name(wav_path.name + ".part")
        soundfile.write(str(partial_path), array, model.sample_rate, subtype="PCM_16", format="WAV")
        os.replace(partial_path, wav_path)

        metadata = params.build_metadata(
            request,
            resolved_seed=seed,
            model_id=self.model_ids[request.kind],
            sample_rate=int(model.sample_rate),
            samples=int(array.shape[0]),
            elapsed_sec=elapsed,
            peak_vram_mib=peak_vram_mib,
            device=self.device,
            wav_name=wav_path.name,
            now=created_at,
        )
        partial_meta = meta_path.with_name(meta_path.name + ".part")
        partial_meta.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(partial_meta, meta_path)
        return GenerationResult(wav_path=wav_path, meta_path=meta_path, metadata=metadata)


def _normalize(normalize_audio: Any, audio: Any, sample_rate: int) -> Any:
    """ラウドネスで音量をそろえる。失敗したらピークでそろえ、理由を標準エラーに出す。"""

    try:
        return normalize_audio(
            audio, strategy="loudness", loudness_compressor=True, sample_rate=sample_rate
        )
    except Exception as error:  # noqa: BLE001 - 上流の失敗の型は決まっていない
        print(f"[acraft] ラウドネス正規化に失敗したためピーク正規化にします: {type(error).__name__}: {error}", file=sys.stderr)
        return normalize_audio(audio, strategy="peak", sample_rate=sample_rate)
