"""AudioCraft のモデルを読み込み、1 回分の生成をして wav と json に書く。

torch / audiocraft / soundfile は、関数の中で import する。こうしておくと、このモジュール自体は
torch なしでも import でき（試験で偽のモデルを差し込める）、環境変数（AUDIOCRAFT_CACHE_DIR など）を
cli 側が先に整えてから重い import が走る。

wav は soundfile で書く。上流の audio_write は ffmpeg の実行ファイルを呼ぶが、この機体には ffmpeg が無い
（sudo が使えず入れられない）ため使わない。正規化だけ上流の normalize_audio を使う。
"""

from __future__ import annotations

import datetime as dt
import io
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


# メロディのモデルを置く場所の名前（種類 music / sound とは別枠。作曲の melody モードだけが使う）。
SLOT_MELODY = "melody"


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


def _reason(error: Exception) -> str:
    """libsndfile のエラー文の最後の部分（"Format not recognised." など）。内部のオブジェクト名を画面に出さない。"""

    text = str(error).strip()
    return text.rsplit(": ", 1)[-1] if text else type(error).__name__


def load_audio_prompt(source: params.SourceAudio) -> LoadedSource:
    """元の音のファイルの中身を soundfile で読む（wav・flac・ogg・mp3）。読めなければ ParamError。

    ffmpeg が無いので torchaudio・av は使わない。リサンプルとチャンネルの変換は上流（convert_audio）に任せる。
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
        melody_model_id: str | None = None,
    ) -> None:
        self.model_ids = {params.KIND_MUSIC: music_model_id, params.KIND_SOUND: sound_model_id}
        self.melody_model_id = melody_model_id
        self.requested_device = device
        self.device = device or "cpu"
        self.outputs_dir = outputs_dir if outputs_dir is not None else config.OUTPUTS_DIR
        self._models: dict[str, Any] = {}
        self._lock = threading.Lock()

    def loaded_kinds(self) -> tuple[str, ...]:
        return tuple(kind for kind in self._models if kind in params.KINDS)

    def melody_loaded(self) -> bool:
        return SLOT_MELODY in self._models

    def available_modes(self, kind: str) -> tuple[str, ...]:
        """いま使えるモード（種類のモデルが無ければ空。メロディのモデルが無ければ melody を除く）。"""

        if kind not in self._models:
            return ()
        return tuple(
            mode for mode in params.KIND_MODES[kind] if mode != params.MODE_MELODY or self.melody_loaded()
        )

    def load(self, kinds: tuple[str, ...] = params.KINDS, melody: bool | None = None) -> None:
        """指定した種類のモデルを読み込む（1 モデルにつき重みの読み込みに数十秒かかる）。

        melody が None なら、作曲を読み込むときにメロディのモデル（ID があれば）も読み込む。
        """

        import torch
        from audiocraft.models import AudioGen, MusicGen

        self.device = self.requested_device or ("cuda" if torch.cuda.is_available() else "cpu")
        if melody is None:
            melody = params.KIND_MUSIC in kinds
        slots: list[tuple[str, str]] = [(kind, self.model_ids[kind]) for kind in kinds]
        if melody and self.melody_model_id:
            slots.append((SLOT_MELODY, self.melody_model_id))
        for slot, model_id in slots:
            started = time.monotonic()
            label = "MusicGen（メロディ）" if slot == SLOT_MELODY else params.KIND_SPECS[slot].model_label
            _log(f"{label} を読み込んでいます: {model_id}（device={self.device}）")
            if slot == params.KIND_SOUND:
                model = AudioGen.get_pretrained(model_id, device=self.device)
            else:
                model = MusicGen.get_pretrained(model_id, device=self.device)
            self._models[slot] = model
            expected_rate = params.KIND_SPECS[params.KIND_SOUND if slot == params.KIND_SOUND else params.KIND_MUSIC].sample_rate
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

        if request.mode == params.MODE_MELODY:
            model = self._models.get(SLOT_MELODY)
            model_id = self.melody_model_id
            if model is None:
                raise RuntimeError(
                    "メロディのモデルが読み込まれていません"
                    f"（{config.MELODY_MODEL_ENV_VAR} が none か、読み込みに失敗しています）。"
                )
        else:
            model = self._models.get(request.kind)
            model_id = self.model_ids[request.kind]
        if model is None:
            raise RuntimeError(f"{request.kind} のモデルが読み込まれていません（読み込み済み: {self.loaded_kinds()}）")
        on_cuda = self.device.startswith("cuda")

        source: LoadedSource | None = None
        if request.mode != params.MODE_TEXT:
            if request.source is None:
                raise params.ParamError(f"モード {request.mode} には元の音が要ります。")
            source = load_audio_prompt(request.source)
            params.check_source_duration(request.kind, request.mode, source.seconds, request.duration)

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
                if source is None:
                    wav = model.generate([request.prompt])
                elif request.mode == params.MODE_CONTINUATION:
                    # 出力は元の音を含み、全体の長さが duration になる。
                    wav = model.generate_continuation(source.waveform, source.sample_rate, [request.prompt])
                else:
                    # メロディは生成する長さまでを使う（上流のデモと同じ）。短ければそのまま。
                    limit = int(source.sample_rate * request.duration)
                    wav = model.generate_with_chroma([request.prompt], source.waveform[..., :limit], source.sample_rate)
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
            stem = params.unique_stem(
                self.outputs_dir, params.build_output_stem(request.kind, seed, created_at, request.mode)
            )
            wav_path, meta_path = params.output_paths(self.outputs_dir, stem)
        wav_path.parent.mkdir(parents=True, exist_ok=True)

        partial_path = wav_path.with_name(wav_path.name + ".part")
        soundfile.write(str(partial_path), array, model.sample_rate, subtype="PCM_16", format="WAV")
        os.replace(partial_path, wav_path)

        # 元の音の写しを出力の隣に残す（何から作ったか後で分かるように）。読んだ値のまま、元のサンプルレートで書く。
        source_path: Path | None = None
        source_info: dict[str, Any] | None = None
        if source is not None and request.source is not None:
            source_path = wav_path.with_name(wav_path.stem + "-source.wav")
            partial_source = source_path.with_name(source_path.name + ".part")
            source_array = np.clip(source.waveform.t().numpy(), -1.0, 1.0)
            soundfile.write(str(partial_source), source_array, source.sample_rate, subtype="PCM_16", format="WAV")
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
            model_id=str(model_id),
            sample_rate=int(model.sample_rate),
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


def _normalize(normalize_audio: Any, audio: Any, sample_rate: int) -> Any:
    """ラウドネスで音量をそろえる。失敗したらピークでそろえ、理由を標準エラーに出す。"""

    try:
        return normalize_audio(
            audio, strategy="loudness", loudness_compressor=True, sample_rate=sample_rate
        )
    except Exception as error:  # noqa: BLE001 - 上流の失敗の型は決まっていない
        print(f"[acraft] ラウドネス正規化に失敗したためピーク正規化にします: {type(error).__name__}: {error}", file=sys.stderr)
        return normalize_audio(audio, strategy="peak", sample_rate=sample_rate)
