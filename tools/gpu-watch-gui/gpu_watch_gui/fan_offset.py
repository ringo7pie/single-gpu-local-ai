"""A30 ファンの補正値（-50〜+50）を fan_host.py へ渡すファイルの書き込み。

GTK には依存しない純 Python。app.py（GTK 層）と tests/test_units.py の両方から import する。
fan_host.py（SettingThisMachine の a30_pico_ubuntu/pc/）は Pico のシリアルポートを排他で開くので、
GUI からは Pico に話せない。代わりに、この GUI が下のファイルを 1 秒ごとに書き直し、fan_host.py が読む。

  {"version": 1, "offset": <整数 -50..50>, "updated": <epoch 秒>}

形式は fan_host.py の read_offset と対応している（変えていない）。補正の意味（自動カーブへの加減・
%ポイント）、有効期限（updated が約 10 秒より古ければ 0）、高温（GPU 70℃ 以上）で下げ側を無視する
保護はすべて fan_host.py 側で決まる。ここは値を丸めて書くだけで、温度は見ない。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

OFFSET_LIMIT = 50
FORMAT_VERSION = 1
DEFAULT_PATH = Path.home() / ".local" / "state" / "a30-fan" / "offset.json"


def clamp_offset(value: float) -> int:
    return max(-OFFSET_LIMIT, min(OFFSET_LIMIT, int(round(value))))


def build_payload(offset: float, now: float) -> dict:
    return {"version": FORMAT_VERSION, "offset": clamp_offset(offset), "updated": now}


def write_offset(path: Path, offset: float, now: float) -> None:
    """一時ファイル→rename で書く（fan_host.py が書きかけの JSON を読まないため）。

    失敗は OSError のまま呼び出し側へ返す。ディレクトリは 0700、ファイルは 0600（自分以外が
    ファンの速度を変えられないようにする）。
    """
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    try:
        descriptor = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(build_payload(offset, now), handle)
        os.replace(tmp_path, path)
    except OSError:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def clear_offset(path: Path) -> None:
    """ファイルを消す。消せなくても何もしない（読み手は約 10 秒で古いと見なして補正 0 に戻る）。"""
    try:
        path.unlink()
    except OSError:
        pass


def load_preference(path: Path) -> int:
    """前回のスライダーの値（起動時に戻す）。無い・壊れている・形が違うときは 0（自動カーブそのまま）。

    fan_host.py へ渡す補正ファイル（write_offset）とは別のファイル。あちらは 10 秒で古いと見なされて 0 に戻るが、
    こちらは次の起動まで残す。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        value = data["offset"]
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if value != value or value in (float("inf"), float("-inf")):  # NaN・無限大
        return 0
    return clamp_offset(value)


def save_preference(path: Path, offset: float) -> None:
    """スライダーの値を保存する。一時ファイル→rename で書き、途中の壊れた JSON を残さない。
    失敗は OSError のまま呼び出し側へ返す。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    try:
        descriptor = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"version": FORMAT_VERSION, "offset": clamp_offset(offset)}, handle)
        os.replace(tmp_path, path)
    except OSError:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
