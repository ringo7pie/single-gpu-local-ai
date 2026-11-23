"""生成パラメータの検証・正規化と、出力ファイル名・メタデータの組み立て（標準ライブラリのみ）。

画面（acraft.ui）と CLI（acraft.cli generate）が同じ検証を通すためにここへ集めてある。
torch も audiocraft も import しないので、素の python で試験できる。
値が不正なときは ParamError を投げる。メッセージは日本語で、どの値がいくつで、
許される範囲がどこかを必ず含める（画面にそのまま出すため）。
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

KIND_MUSIC = "music"
KIND_SOUND = "sound"
KINDS = (KIND_MUSIC, KIND_SOUND)

# 生成のモード。text は説明文だけ、continuation は元の音の続きを作る（出力は元の音を含む）、
# melody は元の音のメロディ（クロマ）に沿って作る（作曲だけ。別モデル musicgen-melody を使う）。
MODE_TEXT = "text"
MODE_CONTINUATION = "continuation"
MODE_MELODY = "melody"
MODES = (MODE_TEXT, MODE_CONTINUATION, MODE_MELODY)
MODE_LABELS = {
    MODE_TEXT: "説明文だけ",
    MODE_CONTINUATION: "元の音の続き",
    MODE_MELODY: "元の音のメロディに沿う",
}
KIND_MODES = {
    KIND_MUSIC: (MODE_TEXT, MODE_CONTINUATION, MODE_MELODY),
    KIND_SOUND: (MODE_TEXT, MODE_CONTINUATION),
}

# 元の音の上限。ファイルの大きさ（REST の base64 を戻した後も同じ）と、読み込んだ音の長さ。
SOURCE_MAX_BYTES = 20 * 1024 * 1024
SOURCE_MAX_SECONDS = 60.0
# 続き生成で、生成する分として最低限残す秒数（元の音は「長さ − これ」以下）。
CONTINUATION_MIN_NEW_SECONDS = 1.0
# 続き生成の元の音の上限（秒）。上流は元の音が min(長さ, 1 回で作れる長さ) 以下であることを要求する
# （MusicGen 30 秒、AudioGen 10 秒）。AudioGen は 10 秒を超える延長で元の音を引き継ぐため、余裕を見て 8 秒にする。
CONTINUATION_MAX_SOURCE_SECONDS = {KIND_MUSIC: 29.0, KIND_SOUND: 8.0}

PROMPT_MAX_CHARS = 500

# seed に -1 を渡すと、生成のたびに乱数で決める。決まった値は出力のメタデータに残る。
SEED_RANDOM = -1
SEED_MAX = 2**31 - 1

TOP_K_MIN, TOP_K_MAX = 0, 2048
TOP_P_MIN, TOP_P_MAX = 0.0, 1.0
TEMPERATURE_MIN, TEMPERATURE_MAX = 0.1, 2.0
CFG_COEF_MIN, CFG_COEF_MAX = 1.0, 10.0

# 上流の既定値（audiocraft の set_generation_params）。
DEFAULT_TOP_K = 250
DEFAULT_TOP_P = 0.0
DEFAULT_TEMPERATURE = 1.0
DEFAULT_CFG_COEF = 3.0


class ParamError(ValueError):
    """生成パラメータが不正なとき。メッセージは利用者向けの日本語。"""


@dataclass(frozen=True)
class KindSpec:
    """作曲・効果音それぞれの、画面と検証で共通に使う値。"""

    kind: str
    label: str
    model_label: str
    sample_rate: int
    default_duration: float
    min_duration: float
    max_duration: float


# 長さの上限: MusicGen は 30 秒まで 1 回で作れる。AudioGen は 10 秒まで 1 回で、それ以上は上流の
# 延長生成（extend_stride）でつなぐため、継ぎ目の品質が落ちうる 20 秒までにする。
# sample_rate は上流のモデルカードの値（MusicGen 32 kHz、AudioGen 16 kHz）。実機試験で wav の値と突き合わせる。
KIND_SPECS: dict[str, KindSpec] = {
    KIND_MUSIC: KindSpec(KIND_MUSIC, "作曲", "MusicGen", 32000, 10.0, 1.0, 30.0),
    KIND_SOUND: KindSpec(KIND_SOUND, "効果音", "AudioGen", 16000, 5.0, 1.0, 20.0),
}


@dataclass(frozen=True)
class SourceAudio:
    """元の音（ファイルの中身そのもの）。音として読めるかは engine が soundfile で確かめる。"""

    name: str
    data: bytes = field(repr=False)


@dataclass(frozen=True)
class GenerationParams:
    """検証済みの生成パラメータ。seed は -1（生成時に乱数で決める）のままのこともある。"""

    kind: str
    prompt: str
    duration: float
    seed: int
    top_k: int
    top_p: float
    temperature: float
    cfg_coef: float
    mode: str = MODE_TEXT
    source: SourceAudio | None = None


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
    kind: str,
    prompt: Any,
    duration: Any,
    seed: Any = SEED_RANDOM,
    top_k: Any = DEFAULT_TOP_K,
    top_p: Any = DEFAULT_TOP_P,
    temperature: Any = DEFAULT_TEMPERATURE,
    cfg_coef: Any = DEFAULT_CFG_COEF,
    mode: Any = MODE_TEXT,
    source: SourceAudio | None = None,
) -> GenerationParams:
    """画面や CLI から来た値を検証して GenerationParams にする。不正なら ParamError。

    元の音の長さの検証は、音を読んでからでないとできないので check_source_duration で別に行う（engine が呼ぶ）。
    """

    spec = KIND_SPECS.get(kind)
    if spec is None:
        raise ParamError(f"種類は {' か '.join(KINDS)} のどちらかにしてください（いまの値: {kind!r}）。")

    if not isinstance(prompt, str):
        raise ParamError(f"説明文は文字列で指定してください（いまの値: {prompt!r}）。")
    # 改行・連続する空白は 1 つの空白にそろえる（モデルの入力は 1 行の文）。
    cleaned_prompt = " ".join(prompt.split())
    if not cleaned_prompt:
        raise ParamError("説明文が空です。作りたい音を英語の短い文で書いてください（例: calm piano melody）。")
    if len(cleaned_prompt) > PROMPT_MAX_CHARS:
        raise ParamError(
            f"説明文が長すぎます（{len(cleaned_prompt)} 文字）。{PROMPT_MAX_CHARS} 文字以下にしてください。"
        )

    duration_value = _as_number("長さ", duration)
    _check_range("長さ", duration_value, spec.min_duration, spec.max_duration, " 秒")

    seed_value = _as_integer("seed", seed)
    if seed_value < SEED_RANDOM or seed_value > SEED_MAX:
        raise ParamError(
            f"seed は {SEED_RANDOM}（毎回乱数）か 0 以上 {SEED_MAX} 以下にしてください（いまの値: {seed_value}）。"
        )

    top_k_value = _as_integer("top_k", top_k)
    _check_range("top_k", top_k_value, TOP_K_MIN, TOP_K_MAX)
    top_p_value = _as_number("top_p", top_p)
    _check_range("top_p", top_p_value, TOP_P_MIN, TOP_P_MAX)
    if top_k_value == 0 and top_p_value == 0.0:
        raise ParamError("top_k と top_p が両方 0 です。どちらかを 0 より大きくしてください（上流の既定は top_k 250）。")
    temperature_value = _as_number("temperature", temperature)
    _check_range("temperature", temperature_value, TEMPERATURE_MIN, TEMPERATURE_MAX)
    cfg_coef_value = _as_number("cfg_coef", cfg_coef)
    _check_range("cfg_coef", cfg_coef_value, CFG_COEF_MIN, CFG_COEF_MAX)

    mode_value = MODE_TEXT if mode is None or mode == "" else mode
    allowed_modes = KIND_MODES[kind]
    if mode_value not in allowed_modes:
        raise ParamError(
            f"{spec.label}のモードは {' / '.join(allowed_modes)} のどれかにしてください（いまの値: {mode_value!r}）。"
        )
    if mode_value == MODE_TEXT:
        if source is not None:
            raise ParamError(
                "元の音が指定されていますが、モードが text（説明文だけ）です。"
                f"元の音を使うときはモードを {' / '.join(allowed_modes[1:])} にしてください。"
            )
    else:
        if source is None:
            raise ParamError(f"モード {mode_value}（{MODE_LABELS[mode_value]}）には元の音（wav・flac・ogg・mp3）が要ります。")
        check_source_size(len(source.data))
        if mode_value == MODE_CONTINUATION and duration_value <= CONTINUATION_MIN_NEW_SECONDS:
            raise ParamError(
                f"続きを作るときの長さ（元の音を含む全体）は {CONTINUATION_MIN_NEW_SECONDS:g} 秒より長くしてください"
                f"（いまの値: {duration_value:g} 秒）。"
            )

    return GenerationParams(
        kind=kind,
        prompt=cleaned_prompt,
        duration=duration_value,
        seed=seed_value,
        top_k=top_k_value,
        top_p=top_p_value,
        temperature=temperature_value,
        cfg_coef=cfg_coef_value,
        mode=mode_value,
        source=source,
    )


def check_source_size(size: int) -> None:
    """元の音のファイルの大きさを確かめる。空や大きすぎるなら ParamError。"""

    if size <= 0:
        raise ParamError("元の音のファイルが空です。")
    if size > SOURCE_MAX_BYTES:
        raise ParamError(
            f"元の音のファイルが大きすぎます（{size / 1024 / 1024:.1f} MB）。"
            f"{SOURCE_MAX_BYTES // (1024 * 1024)} MB 以下にしてください。"
        )


def source_from_path(path: Any) -> SourceAudio | None:
    """画面（Gradio の filepath）から来た元の音を読む。None や空文字なら None。"""

    if path is None or path == "":
        return None
    file_path = Path(str(path))
    try:
        size = file_path.stat().st_size
    except OSError as error:
        raise ParamError(f"元の音のファイルを読めません: {error}") from error
    check_source_size(size)
    return SourceAudio(name=file_path.name, data=file_path.read_bytes())


def source_from_base64(text: Any, name: str = "source") -> SourceAudio:
    """REST の audio_base64 を元の音にする。base64 として読めない・大きすぎるなら ParamError。"""

    if not isinstance(text, str) or not text.strip():
        raise ParamError("audio_base64 は、音のファイル（wav・flac・ogg・mp3）の中身を base64 にした文字列にしてください。")
    # 展開前の長さで先に断る（巨大な文字列を展開してから捨てない）。base64 は 4 文字で 3 バイト。
    if len(text) > (SOURCE_MAX_BYTES * 4) // 3 + 8:
        check_source_size(len(text) * 3 // 4)
    try:
        data = base64.b64decode("".join(text.split()), validate=True)
    except (binascii.Error, ValueError) as error:
        raise ParamError(f"audio_base64 が base64 として読めません: {error}") from error
    check_source_size(len(data))
    return SourceAudio(name=name, data=data)


def check_source_duration(kind: str, mode: str, source_seconds: float, duration: float) -> None:
    """読み込んだ元の音の長さを、モードごとの決まりで確かめる。不正なら ParamError（engine が呼ぶ）。

    melody は元の音が長さより長ければ engine が先頭を切り出すので、全体の上限だけを見る。
    """

    if source_seconds <= 0:
        raise ParamError("元の音の長さが 0 秒です。音の入ったファイルを指定してください。")
    if source_seconds > SOURCE_MAX_SECONDS:
        raise ParamError(
            f"元の音が長すぎます（{source_seconds:.1f} 秒）。{SOURCE_MAX_SECONDS:g} 秒以下にしてください。"
        )
    if mode == MODE_CONTINUATION:
        limit = min(CONTINUATION_MAX_SOURCE_SECONDS[kind], duration - CONTINUATION_MIN_NEW_SECONDS)
        if source_seconds > limit:
            raise ParamError(
                f"続きを作るときは、元の音（{source_seconds:.1f} 秒）を {limit:g} 秒以下にしてください"
                f"（全体の長さ {duration:g} 秒から最低 {CONTINUATION_MIN_NEW_SECONDS:g} 秒を新しく作り、"
                f"{KIND_SPECS[kind].label}では元の音は最長 {CONTINUATION_MAX_SOURCE_SECONDS[kind]:g} 秒）。"
                "長さを延ばすか、元の音を短くしてください。"
            )


# REST の口（POST /api/v1/music・/api/v1/sound）が受け付ける JSON の項目。種類は URL で決まるので本文には書かない。
REST_BODY_FIELDS = (
    "prompt", "duration", "seed", "top_k", "top_p", "temperature", "cfg_coef", "keep", "mode", "audio_base64",
)


@dataclass(frozen=True)
class RestRequest:
    """REST の本文を検証した結果。keep が偽なら、生成した wav と json をサーバーに残さない。"""

    params: GenerationParams
    keep: bool


def parse_rest_body(kind: str, body: Any) -> RestRequest:
    """REST の本文（JSON を読んだもの）を検証する。不正なら ParamError（メッセージは利用者向けの日本語）。

    省略した項目と null は既定値になる。未知の項目は、綴りの間違いに気づけるよう拒否する。
    """

    spec = KIND_SPECS.get(kind)
    if spec is None:
        raise ParamError(f"種類は {' か '.join(KINDS)} のどちらかにしてください（いまの値: {kind!r}）。")
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
        raise ParamError(f"mode は文字列（{' / '.join(KIND_MODES[kind])}）にしてください（いまの値: {mode!r}）。")
    audio_base64 = body.get("audio_base64")
    source = None if audio_base64 is None else source_from_base64(audio_base64, name="rest-source")

    request_params = normalize_params(
        kind,
        body["prompt"],
        pick("duration", spec.default_duration),
        pick("seed", SEED_RANDOM),
        pick("top_k", DEFAULT_TOP_K),
        pick("top_p", DEFAULT_TOP_P),
        pick("temperature", DEFAULT_TEMPERATURE),
        pick("cfg_coef", DEFAULT_CFG_COEF),
        mode,
        source,
    )
    return RestRequest(params=request_params, keep=keep)


def build_response_headers(metadata: dict[str, Any], keep: bool) -> dict[str, str]:
    """wav を返すときの HTTP ヘッダ。実際に使われた seed・長さ・所要時間などを載せる（本文は wav だけなので）。"""

    headers = {
        "X-Acraft-Kind": str(metadata["kind"]),
        "X-Acraft-Mode": str(metadata.get("mode", MODE_TEXT)),
        "X-Acraft-Model": str(metadata["model"]),
        "X-Acraft-Seed": str(metadata["seed"]),
        "X-Acraft-Sample-Rate": str(metadata["sample_rate"]),
        "X-Acraft-Duration-Sec": str(metadata["duration_actual_sec"]),
        "X-Acraft-Elapsed-Sec": str(metadata["elapsed_sec"]),
        "Content-Disposition": f'attachment; filename="{metadata["wav"]}"',
    }
    if keep:
        headers["X-Acraft-File"] = str(metadata["wav"])
    return headers


def resolve_seed(seed: int, rng: random.Random | None = None) -> int:
    """seed が -1 なら乱数で決めた値、それ以外はそのまま返す。"""

    if seed != SEED_RANDOM:
        return seed
    generator = rng if rng is not None else random.SystemRandom()
    return generator.randint(0, SEED_MAX)


def build_output_stem(kind: str, seed: int, now: dt.datetime, mode: str = MODE_TEXT) -> str:
    """出力ファイルの拡張子を除いた名前。例: 20261027-153012-music-12345、20261123-101500-music-melody-12345"""

    mode_part = "" if mode == MODE_TEXT else f"-{mode}"
    return f"{now:%Y%m%d-%H%M%S}-{kind}{mode_part}-{seed}"


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
    samples: int,
    elapsed_sec: float,
    peak_vram_mib: float | None,
    device: str,
    wav_name: str,
    now: dt.datetime,
    source_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """出力の隣に置く json の中身。入力・実際に使った seed・モデル・所要時間を残す。

    source_info は元の音を使ったときだけ（元のファイル名・長さ・サンプルレート・チャンネル数・残した写しの名前）。
    """

    return {
        "kind": params.kind,
        "mode": params.mode,
        "source": source_info,
        "prompt": params.prompt,
        "duration_requested_sec": params.duration,
        "duration_actual_sec": round(samples / sample_rate, 3) if sample_rate else None,
        "seed": resolved_seed,
        "top_k": params.top_k,
        "top_p": params.top_p,
        "temperature": params.temperature,
        "cfg_coef": params.cfg_coef,
        "model": model_id,
        "sample_rate": sample_rate,
        "samples": samples,
        "elapsed_sec": round(elapsed_sec, 2),
        "peak_vram_mib": None if peak_vram_mib is None else round(peak_vram_mib, 1),
        "device": device,
        "wav": wav_name,
        "created_at": now.isoformat(timespec="seconds"),
    }


def list_recent_outputs(outputs_dir: Path, limit: int = 20) -> list[dict[str, Any]]:
    """outputs/ のメタデータ json を新しい順に読み、画面の一覧用の行にする。

    壊れた json・wav が消えているものは飛ばす。ディレクトリが無ければ空。
    """

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
                "kind": meta.get("kind", ""),
                "mode": meta.get("mode", MODE_TEXT),
                "prompt": meta.get("prompt", ""),
                "duration": meta.get("duration_actual_sec"),
                "seed": meta.get("seed"),
                "created_at": meta.get("created_at", ""),
            }
        )
        if len(rows) >= limit:
            break
    return rows
