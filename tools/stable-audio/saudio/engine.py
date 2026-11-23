"""Stable Audio 3.0 Medium を読み込み、1 回分の生成をして wav と json に書く。

torch / 上流（stable_audio_3）/ soundfile は関数の中で import する。こうしておくと、このモジュール自体は
torch なしでも import でき（試験で偽物を差し込める）、cli 側が環境変数（HF_HOME・HF_HUB_OFFLINE など）を
先に整えてから重い import が走る。

音の読み書きは soundfile で行う。この機体には ffmpeg が無く、torchaudio の読み込みは使わない。
"""

from __future__ import annotations

import datetime as dt
import io
import json
import os
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
    source_path: Path | None = None


@dataclass(frozen=True)
class LoadedSource:
    """soundfile で読んだ元の音。waveform は [チャンネル, サンプル] の float32（torch.Tensor）。"""

    waveform: Any
    sample_rate: int
    channels: int
    frames: int

    @property
    def seconds(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate else 0.0


def _log(message: str) -> None:
    print(f"[saudio] {message}", flush=True)


def _reason(error: Exception) -> str:
    """libsndfile のエラー文の最後の部分（"Format not recognised." など）。内部のオブジェクト名を画面に出さない。"""

    text = str(error).strip()
    return text.rsplit(": ", 1)[-1] if text else type(error).__name__


def load_audio_source(source: params.SourceAudio) -> LoadedSource:
    """元の音のファイルの中身を soundfile で読む（wav・flac・ogg・mp3）。読めなければ ParamError。

    リサンプル（44.1 kHz へ）とチャンネルの変換（ステレオへ）は上流（prepare_audio）に任せる。
    """

    import soundfile
    import torch

    try:
        data, sample_rate = soundfile.read(io.BytesIO(source.data), dtype="float32", always_2d=True)
    except Exception as error:  # noqa: BLE001 - libsndfile の失敗の型は決まっていない
        raise params.ParamError(
            f"元の音を音声として読めません（{source.name}）。wav・flac・ogg・mp3 を指定してください"
            f"（{_reason(error)}）。"
        ) from error
    frames, channels = data.shape
    waveform = torch.from_numpy(data.T.copy())
    return LoadedSource(waveform=waveform, sample_rate=int(sample_rate), channels=int(channels), frames=int(frames))


class Engine:
    """モデルを常駐させて生成する。生成は 1 度に 1 件（GPU が 1 枚なので Lock で直列にする）。"""

    def __init__(self, device: str | None = None, outputs_dir: Path | None = None) -> None:
        self.model_id = config.MODEL_REPO
        self.requested_device = device
        self.device = device or "cpu"
        self.outputs_dir = outputs_dir if outputs_dir is not None else config.OUTPUTS_DIR
        self._model: Any = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """モデルを読み込む（重み 9.2 GB と文章の符号化器 1.2 GB）。"""

        import torch
        from stable_audio_3 import StableAudioModel

        self.device = self.requested_device or ("cuda" if torch.cuda.is_available() else "cpu")
        started = time.monotonic()
        _log(f"Stable Audio 3.0 {config.MODEL_NAME} を読み込んでいます: {self.model_id}（device={self.device}）")
        self._model = StableAudioModel.from_pretrained(config.MODEL_NAME, device=self.device)
        rate = int(self._model.model.sample_rate)
        note = "" if rate == params.SAMPLE_RATE else f"（想定 {params.SAMPLE_RATE} Hz と違います）"
        _log(f"  読み込み完了 {time.monotonic() - started:.1f} 秒、sample_rate={rate} Hz{note}")
        if self.device.startswith("cuda"):
            _log(f"  VRAM 割り当て {torch.cuda.memory_allocated() / _MIB:.0f} MiB（読み込み後）")

    def generate(
        self,
        request: params.GenerationParams,
        out_path: Path | None = None,
        now: dt.datetime | None = None,
    ) -> GenerationResult:
        """1 件を生成して wav と json に書く。元の音の誤りは ParamError、読み込んでいなければ RuntimeError。"""

        import numpy as np
        import soundfile
        import torch

        model = self._model
        if model is None:
            raise RuntimeError("モデルが読み込まれていません。")
        on_cuda = self.device.startswith("cuda")

        source: LoadedSource | None = None
        if request.mode != params.MODE_TEXT:
            if request.source is None:
                raise params.ParamError(f"モード {request.mode} には元の音が要ります。")
            source = load_audio_source(request.source)
            params.check_source(request, source.seconds)

        kwargs: dict[str, Any] = {}
        if source is not None and request.mode == params.MODE_AUDIO:
            # 上流の init_audio は (サンプルレート, [チャンネル, サンプル]) の組（docs/workflows/inference.md）。
            kwargs["init_audio"] = (source.sample_rate, source.waveform)
            kwargs["init_noise_level"] = request.init_noise_level
        elif source is not None and request.mode == params.MODE_INPAINT:
            kwargs["inpaint_audio"] = (source.sample_rate, source.waveform)
            kwargs["inpaint_mask_start_seconds"] = float(request.inpaint_start)
            kwargs["inpaint_mask_end_seconds"] = float(
                request.inpaint_end if request.inpaint_end is not None else request.duration
            )

        with self._lock:
            seed = params.resolve_seed(request.seed)
            if on_cuda:
                torch.cuda.reset_peak_memory_stats()
            started = time.monotonic()
            audio = model.generate(
                prompt=request.prompt,
                duration=request.duration,
                steps=request.steps,
                seed=seed,
                batch_size=1,
                # 上流の generate の既定 sample_size（5,292,032 = 120 秒）だと、長さを 380 秒にしても 120 秒で切れる
                # （2026-11-23 に実測）。上流の CLI と同じく、モデルの設定の値（medium は 16,777,216 = 約 380.4 秒）を渡す。
                sample_size=int(model.model_config["sample_size"]),
                **kwargs,
            )
            if on_cuda:
                torch.cuda.synchronize()
            elapsed = time.monotonic() - started
            peak_vram_mib = torch.cuda.max_memory_allocated() / _MIB if on_cuda else None

        sample_rate = int(model.model.sample_rate)
        array = np.clip(audio[0].detach().float().cpu().t().numpy(), -1.0, 1.0)

        created_at = now if now is not None else dt.datetime.now()
        if out_path is not None:
            wav_path = out_path if out_path.suffix == ".wav" else out_path.with_name(out_path.name + ".wav")
            meta_path = wav_path.with_suffix(".json")
        else:
            stem = params.unique_stem(self.outputs_dir, params.build_output_stem(request.mode, seed, created_at))
            wav_path, meta_path = params.output_paths(self.outputs_dir, stem)
        wav_path.parent.mkdir(parents=True, exist_ok=True)

        partial_path = wav_path.with_name(wav_path.name + ".part")
        soundfile.write(str(partial_path), array, sample_rate, subtype="PCM_16", format="WAV")
        os.replace(partial_path, wav_path)

        # 元の音の写しを出力の隣に残す（何から作ったか後で分かるように）。読んだ値のまま、元のサンプルレートで書く。
        source_path: Path | None = None
        source_info: dict[str, Any] | None = None
        if source is not None and request.source is not None:
            source_path = wav_path.with_name(wav_path.stem + "-source.wav")
            partial_source = source_path.with_name(source_path.name + ".part")
            soundfile.write(
                str(partial_source), np.clip(source.waveform.t().numpy(), -1.0, 1.0), source.sample_rate,
                subtype="PCM_16", format="WAV",
            )
            os.replace(partial_source, source_path)
            source_info = {
                "name": request.source.name,
                "duration_sec": round(source.seconds, 3),
                "sample_rate": source.sample_rate,
                "channels": source.channels,
                "wav": source_path.name,
            }

        metadata = params.build_metadata(
            request,
            resolved_seed=seed,
            model_id=self.model_id,
            sample_rate=sample_rate,
            channels=int(array.shape[1]),
            samples=int(array.shape[0]),
            elapsed_sec=elapsed,
            peak_vram_mib=peak_vram_mib,
            device=self.device,
            wav_name=wav_path.name,
            now=created_at,
            source_info=source_info,
        )
        partial_meta = meta_path.with_name(meta_path.name + ".part")
        partial_meta.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(partial_meta, meta_path)
        return GenerationResult(wav_path=wav_path, meta_path=meta_path, metadata=metadata, source_path=source_path)
