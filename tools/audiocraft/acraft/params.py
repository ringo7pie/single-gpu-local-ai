"""生成パラメータの検証・正規化と、出力ファイル名・メタデータの組み立て（標準ライブラリのみ）。

画面（acraft.ui）と CLI（acraft.cli generate）が同じ検証を通すためにここへ集めてある。
torch も audiocraft も import しないので、素の python で試験できる。
値が不正なときは ParamError を投げる。メッセージは日本語で、どの値がいくつで、
許される範囲がどこかを必ず含める（画面にそのまま出すため）。
"""

from __future__ import annotations

import datetime as dt
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

KIND_MUSIC = "music"
KIND_SOUND = "sound"
KINDS = (KIND_MUSIC, KIND_SOUND)

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
) -> GenerationParams:
    """画面や CLI から来た値を検証して GenerationParams にする。不正なら ParamError。"""

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

    return GenerationParams(
        kind=kind,
        prompt=cleaned_prompt,
        duration=duration_value,
        seed=seed_value,
        top_k=top_k_value,
        top_p=top_p_value,
        temperature=temperature_value,
        cfg_coef=cfg_coef_value,
    )


# REST の口（POST /api/v1/music・/api/v1/sound）が受け付ける JSON の項目。種類は URL で決まるので本文には書かない。
REST_BODY_FIELDS = ("prompt", "duration", "seed", "top_k", "top_p", "temperature", "cfg_coef", "keep")


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

    request_params = normalize_params(
        kind,
        body["prompt"],
        pick("duration", spec.default_duration),
        pick("seed", SEED_RANDOM),
        pick("top_k", DEFAULT_TOP_K),
        pick("top_p", DEFAULT_TOP_P),
        pick("temperature", DEFAULT_TEMPERATURE),
        pick("cfg_coef", DEFAULT_CFG_COEF),
    )
    return RestRequest(params=request_params, keep=keep)


def build_response_headers(metadata: dict[str, Any], keep: bool) -> dict[str, str]:
    """wav を返すときの HTTP ヘッダ。実際に使われた seed・長さ・所要時間などを載せる（本文は wav だけなので）。"""

    headers = {
        "X-Acraft-Kind": str(metadata["kind"]),
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


def build_output_stem(kind: str, seed: int, now: dt.datetime) -> str:
    """出力ファイルの拡張子を除いた名前。例: 20261027-153012-music-12345"""

    return f"{now:%Y%m%d-%H%M%S}-{kind}-{seed}"


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
) -> dict[str, Any]:
    """出力の隣に置く json の中身。入力・実際に使った seed・モデル・所要時間を残す。"""

    return {
        "kind": params.kind,
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
                "prompt": meta.get("prompt", ""),
                "duration": meta.get("duration_actual_sec"),
                "seed": meta.get("seed"),
                "created_at": meta.get("created_at", ""),
            }
        )
        if len(rows) >= limit:
            break
    return rows
