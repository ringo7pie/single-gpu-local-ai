"""生成パラメータの検証・正規化と、出力ファイル名・メタデータの組み立て（標準ライブラリのみ）。

画面（saudio.ui）・REST（saudio.rest）・CLI（saudio.cli generate）が同じ検証を通すためにここへ集めてある。
torch も上流も import しないので、素の python で試験できる。値が不正なときは ParamError を投げる。
メッセージは日本語で、どの値がいくつで、許される範囲がどこかを含める（画面にそのまま出すため）。

モードは 3 つ（上流 StableAudioModel.generate の引数に対応）:
    text     文章 → 音。prompt と duration だけ
    audio    音 → 音（init_audio）。元の音にノイズを足してから、説明文に沿って作り直す。init_noise_level が小さいほど元に近い
    inpaint  部分の作り直し・続き（inpaint_audio）。元の音の [開始, 終了) 秒だけを作り直し、ほかは残す。
             開始を元の音の長さにし、長さ（duration）を元の音より長くすると「続き」になる
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MODE_TEXT = "text"
MODE_AUDIO = "audio"
MODE_INPAINT = "inpaint"
MODES = (MODE_TEXT, MODE_AUDIO, MODE_INPAINT)
MODE_LABELS = {
    MODE_TEXT: "文章 → 音",
    MODE_AUDIO: "音 → 音（作り変え）",
    MODE_INPAINT: "部分の作り直し・続き",
}

PROMPT_MAX_CHARS = 1000

# 出力は 44.1 kHz ステレオ（上流 README の SAME 音声圧縮モデルの値。実機で wav と突き合わせる）。
SAMPLE_RATE = 44100
CHANNELS = 2
# 長さ。Medium は最長 380 秒（上流 README の表）。既定は 30 秒（上流の既定 120 秒は待ちが長い）。
MIN_DURATION = 1.0
MAX_DURATION = 380.0
DEFAULT_DURATION = 30.0

# 上流の既定（docs/workflows/inference.md）。post-trained の medium では steps 8 が既定で、増やしても良くなるとは限らない。
# cfg_scale と negative_prompt は base モデルにしか効かない（同文書）ので、画面にも REST にも出さない。
DEFAULT_STEPS = 8
STEPS_MIN, STEPS_MAX = 1, 100
DEFAULT_INIT_NOISE_LEVEL = 0.7
INIT_NOISE_MIN, INIT_NOISE_MAX = 0.0, 1.0

SEED_RANDOM = -1
SEED_MAX = 2**31 - 1

# 元の音の上限。ファイルの大きさ（REST の base64 を戻した後も同じ）と、読み込んだ音の長さ。
SOURCE_MAX_BYTES = 50 * 1024 * 1024
SOURCE_MAX_SECONDS = MAX_DURATION


class ParamError(ValueError):
    """生成パラメータが不正なとき。メッセージは利用者向けの日本語。"""


@dataclass(frozen=True)
class SourceAudio:
    """元の音（ファイルの中身そのもの）。音として読めるかは engine が soundfile で確かめる。"""

    name: str
    data: bytes = field(repr=False)


@dataclass(frozen=True)
class GenerationParams:
    """検証済みの生成パラメータ。seed は -1（生成時に乱数で決める）のままのこともある。

    inpaint_start / inpaint_end は inpaint のときだけ使う。inpaint_end が None なら「長さ」まで（続きのときに便利）。
    """

    prompt: str
    duration: float
    seed: int
    steps: int
    mode: str = MODE_TEXT
    source: SourceAudio | None = None
    init_noise_level: float = DEFAULT_INIT_NOISE_LEVEL
    inpaint_start: float | None = None
    inpaint_end: float | None = None


def _as_number(label: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParamError(f"{label} は数で指定してください（いまの値: {value!r}）。")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ParamError(f"{label} は有限の数で指定してください（いまの値: {value!r}）。")
    return number


def _as_integer(label: str, value: Any) -> int:
    number = _as_number(label, value)
    if number != int(number):
        raise ParamError(f"{label} は整数で指定してください（いまの値: {value!r}）。")
    return int(number)


def _check_range(label: str, number: float, lower: float, upper: float, unit: str = "") -> None:
    if number < lower or number > upper:
        raise ParamError(
            f"{label} は {lower:g}{unit}以上 {upper:g}{unit}以下にしてください（いまの値: {number:g}{unit}）。"
        )


def normalize_params(
    prompt: Any,
    duration: Any = DEFAULT_DURATION,
    seed: Any = SEED_RANDOM,
    steps: Any = DEFAULT_STEPS,
    mode: Any = MODE_TEXT,
    source: SourceAudio | None = None,
    init_noise_level: Any = DEFAULT_INIT_NOISE_LEVEL,
    inpaint_start: Any = None,
    inpaint_end: Any = None,
) -> GenerationParams:
    """画面・REST・CLI から来た値を検証して GenerationParams にする。不正なら ParamError。

    元の音の長さに関わる検証は、音を読んでからでないとできないので check_source で別に行う（engine が呼ぶ）。
    """

    if not isinstance(prompt, str):
        raise ParamError(f"説明文は文字列で指定してください（いまの値: {prompt!r}）。")
    cleaned_prompt = " ".join(prompt.split())
    if not cleaned_prompt:
        raise ParamError("説明文が空です。作りたい音を英語の文で書いてください（例: calm piano melody）。")
    if len(cleaned_prompt) > PROMPT_MAX_CHARS:
        raise ParamError(f"説明文が長すぎます（{len(cleaned_prompt)} 文字）。{PROMPT_MAX_CHARS} 文字以下にしてください。")

    duration_value = _as_number("長さ", duration)
    _check_range("長さ", duration_value, MIN_DURATION, MAX_DURATION, " 秒")

    seed_value = _as_integer("seed", seed)
    if seed_value < SEED_RANDOM or seed_value > SEED_MAX:
        raise ParamError(f"seed は {SEED_RANDOM}（毎回乱数）か 0 以上 {SEED_MAX} 以下にしてください（いまの値: {seed_value}）。")

    steps_value = _as_integer("steps", steps)
    _check_range("steps", steps_value, STEPS_MIN, STEPS_MAX)

    mode_value = MODE_TEXT if mode is None or mode == "" else mode
    if mode_value not in MODES:
        raise ParamError(f"モードは {' / '.join(MODES)} のどれかにしてください（いまの値: {mode_value!r}）。")

    noise_value = _as_number("init_noise_level", DEFAULT_INIT_NOISE_LEVEL if init_noise_level is None else init_noise_level)
    _check_range("init_noise_level", noise_value, INIT_NOISE_MIN, INIT_NOISE_MAX)

    start_value: float | None = None
    end_value: float | None = None
    if mode_value == MODE_TEXT:
        if source is not None:
            raise ParamError("元の音が指定されていますが、モードが text（文章 → 音）です。元の音を使うときはモードを audio か inpaint にしてください。")
    else:
        if source is None:
            raise ParamError(f"モード {mode_value}（{MODE_LABELS[mode_value]}）には元の音（wav・flac・ogg・mp3）が要ります。")
        check_source_size(len(source.data))
    if mode_value == MODE_INPAINT:
        if inpaint_start is None:
            raise ParamError("inpaint には作り直す範囲の開始（秒）が要ります。続きを作るときは元の音の長さを開始にします。")
        start_value = _as_number("作り直す範囲の開始", inpaint_start)
        if start_value < 0:
            raise ParamError(f"作り直す範囲の開始は 0 秒以上にしてください（いまの値: {start_value:g} 秒）。")
        if inpaint_end is not None:
            end_value = _as_number("作り直す範囲の終了", inpaint_end)
        effective_end = duration_value if end_value is None else end_value
        if effective_end <= start_value:
            raise ParamError(
                f"作り直す範囲の終了（{effective_end:g} 秒）は開始（{start_value:g} 秒）より後にしてください。"
            )
        if effective_end > duration_value:
            raise ParamError(
                f"作り直す範囲の終了（{effective_end:g} 秒）は長さ（{duration_value:g} 秒）以下にしてください。"
            )

    return GenerationParams(
        prompt=cleaned_prompt,
        duration=duration_value,
        seed=seed_value,
        steps=steps_value,
        mode=mode_value,
        source=source,
        init_noise_level=noise_value,
        inpaint_start=start_value,
        inpaint_end=end_value,
    )


def check_source_size(size: int) -> None:
    """元の音のファイルの大きさを確かめる。空や大きすぎるなら ParamError。"""

    if size <= 0:
        raise ParamError("元の音のファイルが空です。")
    if size > SOURCE_MAX_BYTES:
        raise ParamError(
            f"元の音のファイルが大きすぎます（{size / 1024 / 1024:.1f} MB）。{SOURCE_MAX_BYTES // (1024 * 1024)} MB 以下にしてください。"
        )


def source_from_path(path: Any) -> SourceAudio | None:
    """画面（Gradio の filepath）や CLI から来た元の音を読む。None や空文字なら None。"""

    if path is None or path == "":
        return None
    file_path = Path(str(path))
    try:
        size = file_path.stat().st_size
    except OSError as error:
        raise ParamError(f"元の音のファイルを読めません: {error}") from error
    check_source_size(size)
    return SourceAudio(name=file_path.name, data=file_path.read_bytes())


def source_from_base64(text: Any, name: str = "rest-source") -> SourceAudio:
    """REST の audio_base64 を元の音にする。base64 として読めない・大きすぎるなら ParamError。"""

    if not isinstance(text, str) or not text.strip():
        raise ParamError("audio_base64 は、音のファイル（wav・flac・ogg・mp3）の中身を base64 にした文字列にしてください。")
    if len(text) > (SOURCE_MAX_BYTES * 4) // 3 + 8:
        check_source_size(len(text) * 3 // 4)
    try:
        data = base64.b64decode("".join(text.split()), validate=True)
    except (binascii.Error, ValueError) as error:
        raise ParamError(f"audio_base64 が base64 として読めません: {error}") from error
    check_source_size(len(data))
    return SourceAudio(name=name, data=data)


def check_source(request: GenerationParams, source_seconds: float) -> None:
    """読み込んだ元の音の長さを確かめる。不正なら ParamError（engine が呼ぶ）。

    audio は元の音を長さに合わせて切る・無音で延ばす（上流の prepare_audio）ので、全体の上限だけを見る。
    inpaint は作り直さない部分が元の音から来るので、開始が元の音の長さを超えると間が無音になる。それを断る。
    """

    if source_seconds <= 0:
        raise ParamError("元の音の長さが 0 秒です。音の入ったファイルを指定してください。")
    if source_seconds > SOURCE_MAX_SECONDS:
        raise ParamError(f"元の音が長すぎます（{source_seconds:.1f} 秒）。{SOURCE_MAX_SECONDS:g} 秒以下にしてください。")
    if request.mode == MODE_INPAINT and request.inpaint_start is not None:
        # 開始を元の音の長さちょうどにする「続き」を許すため、わずかな誤差（0.05 秒）は見逃す。
        if request.inpaint_start > source_seconds + 0.05:
            raise ParamError(
                f"作り直す範囲の開始（{request.inpaint_start:g} 秒）が、元の音の長さ（{source_seconds:.2f} 秒）を超えています。"
                "続きを作るときは、開始を元の音の長さ以下にしてください。"
            )


# REST の口（POST /api/v1/generate）が受け付ける JSON の項目。
REST_BODY_FIELDS = (
    "prompt", "duration", "seed", "steps", "mode", "audio_base64", "init_noise_level", "inpaint_start", "inpaint_end", "keep",
)


@dataclass(frozen=True)
class RestRequest:
    """REST の本文を検証した結果。keep が偽なら、生成した wav と json をサーバーに残さない。"""

    params: GenerationParams
    keep: bool


def parse_rest_body(body: Any) -> RestRequest:
    """REST の本文（JSON を読んだもの）を検証する。不正なら ParamError。省略と null は既定値。未知の項目は拒否する。"""

    if not isinstance(body, dict):
        raise ParamError("リクエストの本文は JSON のオブジェクト（{...}）にしてください。")
    unknown = sorted(str(key) for key in body if key not in REST_BODY_FIELDS)
    if unknown:
        raise ParamError(f"未知の項目があります: {', '.join(unknown)}（使える項目: {', '.join(REST_BODY_FIELDS)}）。")
    if body.get("prompt") is None:
        raise ParamError("prompt（英語の説明文）が要ります。")

    def pick(name: str, default: Any) -> Any:
        value = body.get(name)
        return default if value is None else value

    keep = pick("keep", True)
    if not isinstance(keep, bool):
        raise ParamError(f"keep は true か false にしてください（いまの値: {keep!r}）。")
    mode = pick("mode", MODE_TEXT)
    if not isinstance(mode, str):
        raise ParamError(f"mode は文字列（{' / '.join(MODES)}）にしてください（いまの値: {mode!r}）。")
    audio_base64 = body.get("audio_base64")
    source = None if audio_base64 is None else source_from_base64(audio_base64)
    request_params = normalize_params(
        body["prompt"],
        pick("duration", DEFAULT_DURATION),
        pick("seed", SEED_RANDOM),
        pick("steps", DEFAULT_STEPS),
        mode,
        source,
        pick("init_noise_level", DEFAULT_INIT_NOISE_LEVEL),
        body.get("inpaint_start"),
        body.get("inpaint_end"),
    )
    return RestRequest(params=request_params, keep=keep)


def build_response_headers(metadata: dict[str, Any], keep: bool) -> dict[str, str]:
    """wav を返すときの HTTP ヘッダ。実際に使われた seed・長さ・所要時間などを載せる。"""

    headers = {
        "X-Saudio-Mode": str(metadata["mode"]),
        "X-Saudio-Model": str(metadata["model"]),
        "X-Saudio-Seed": str(metadata["seed"]),
        "X-Saudio-Sample-Rate": str(metadata["sample_rate"]),
        "X-Saudio-Channels": str(metadata["channels"]),
        "X-Saudio-Duration-Sec": str(metadata["duration_actual_sec"]),
        "X-Saudio-Elapsed-Sec": str(metadata["elapsed_sec"]),
        "Content-Disposition": f'attachment; filename="{metadata["wav"]}"',
    }
    if keep:
        headers["X-Saudio-File"] = str(metadata["wav"])
    return headers


def resolve_seed(seed: int, rng: random.Random | None = None) -> int:
    """seed が -1 なら乱数で決めた値、それ以外はそのまま返す。"""

    if seed != SEED_RANDOM:
        return seed
    generator = rng if rng is not None else random.SystemRandom()
    return generator.randint(0, SEED_MAX)


def build_output_stem(mode: str, seed: int, now: dt.datetime) -> str:
    """出力ファイルの拡張子を除いた名前。例: 20261123-153012-text-12345"""

    return f"{now:%Y%m%d-%H%M%S}-{mode}-{seed}"


def unique_stem(outputs_dir: Path, stem: str) -> str:
    """同じ名前の出力が既にあれば、末尾に -2、-3 … を付けて重ならない名前にする。"""

    candidate = stem
    index = 2
    while (outputs_dir / f"{candidate}.wav").exists() or (outputs_dir / f"{candidate}.json").exists():
        candidate = f"{stem}-{index}"
        index += 1
    return candidate


def output_paths(outputs_dir: Path, stem: str) -> tuple[Path, Path]:
    """(wav のパス, メタデータ json のパス)。"""

    return outputs_dir / f"{stem}.wav", outputs_dir / f"{stem}.json"


def build_metadata(
    params: GenerationParams,
    *,
    resolved_seed: int,
    model_id: str,
    sample_rate: int,
    channels: int,
    samples: int,
    elapsed_sec: float,
    peak_vram_mib: float | None,
    device: str,
    wav_name: str,
    now: dt.datetime,
    source_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """出力の隣に置く json の中身。入力・実際に使った seed・モデル・所要時間を残す。"""

    return {
        "mode": params.mode,
        "prompt": params.prompt,
        "duration_requested_sec": params.duration,
        "duration_actual_sec": round(samples / sample_rate, 3) if sample_rate else None,
        "seed": resolved_seed,
        "steps": params.steps,
        "init_noise_level": params.init_noise_level if params.mode == MODE_AUDIO else None,
        "inpaint_start_sec": params.inpaint_start,
        "inpaint_end_sec": (params.inpaint_end if params.inpaint_end is not None else params.duration)
        if params.mode == MODE_INPAINT
        else None,
        "source": source_info,
        "model": model_id,
        "sample_rate": sample_rate,
        "channels": channels,
        "samples": samples,
        "elapsed_sec": round(elapsed_sec, 2),
        "peak_vram_mib": None if peak_vram_mib is None else round(peak_vram_mib, 1),
        "device": device,
        "wav": wav_name,
        "created_at": now.isoformat(timespec="seconds"),
    }


def list_recent_outputs(outputs_dir: Path, limit: int = 20) -> list[dict[str, Any]]:
    """outputs/ のメタデータ json を新しい順に読み、画面の一覧用の行にする。壊れた json・wav が消えたものは飛ばす。"""

    if not outputs_dir.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for meta_path in sorted(outputs_dir.glob("*.json"), reverse=True):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(meta, dict) or not (outputs_dir / str(meta.get("wav", ""))).is_file():
            continue
        rows.append(
            {
                "wav": meta["wav"],
                "mode": meta.get("mode", ""),
                "prompt": meta.get("prompt", ""),
                "duration": meta.get("duration_actual_sec"),
                "seed": meta.get("seed"),
                "created_at": meta.get("created_at", ""),
            }
        )
        if len(rows) >= limit:
            break
    return rows
