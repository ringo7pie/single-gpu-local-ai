"""Tapo の読み取り役（tapo_power.py）を GPU Watch から使うための、GTK 非依存の部品。

パスワードはどこにも保存しない。ログイン画面で受け取った値は GPU Watch のメモリにだけ置き、
読み取り役の標準入力（1 行の JSON）で渡す。ファイルに残すのは IP とメールアドレスだけ
（state/tapo.json。次回のログイン画面の初期値にするため。パスワードは含めない）。
"""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
from typing import Dict, List, Tuple

GUI_DIR = Path(__file__).resolve().parent.parent
READER_SCRIPT = GUI_DIR / "tapo_power.py"
# `tapo`（PyPI）を入れた専用 venv。システムの python3 には入れない（setup-tapo.sh が作る）。
DEFAULT_PYTHON = Path.home() / ".local" / "share" / "tapo-meter" / ".venv" / "bin" / "python"
# 機種・IP・メールアドレスは機械ごとの値なので git 管理外の state/ に置く。
PROFILE_FILE = GUI_DIR / "state" / "tapo.json"
DEFAULT_INTERVAL_SEC = 5.0

# tapo_power.py の終了コードと揃える（試験で一致を確認する）
EXIT_BAD_CREDENTIALS = 3
EXIT_FIRST_READ_FAILED = 4


def build_argv(python_path: Path, interval_sec: float) -> List[str]:
    """-I（隔離モード）で起動する。カレントや PYTHONPATH にあるファイルを読み込ませない。"""
    return [str(python_path), "-I", str(READER_SCRIPT), "--raw", "--interval", f"{interval_sec:g}"]


def reader_available(python_path: Path) -> Tuple[bool, str]:
    """読み取り役を起動できるか。できなければ理由（導入の案内）を返す。"""
    if not os.access(python_path, os.X_OK):
        return False, f"tapo の専用 venv がありません: {python_path}（setup-tapo.sh で作る）"
    if not READER_SCRIPT.exists():
        return False, f"tapo_power.py が見つかりません: {READER_SCRIPT}"
    return True, ""


def validate_host(text: str) -> Tuple[bool, str]:
    """IP アドレスだけを受け付ける（ホスト名や URL は探索（UDP）に使えない）。"""
    candidate = text.strip()
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return False, "プラグの IP アドレスを入力してください（例 192.168.8.128）"
    return True, candidate


def encode_credentials(host: str, username: str, password: str) -> bytes:
    """読み取り役の標準入力へ渡す 1 行の JSON。末尾に改行を付ける。"""
    return (json.dumps({"host": host, "username": username, "password": password}, ensure_ascii=False) + "\n").encode("utf-8")


def load_profile(path: Path = PROFILE_FILE) -> Dict[str, str]:
    """前回入力した IP とメールアドレス。無い・壊れているときは空（ログイン画面を空欄で出す）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"host": "", "username": ""}
    if not isinstance(data, dict):
        return {"host": "", "username": ""}
    return {"host": str(data.get("host", "")), "username": str(data.get("username", ""))}


def save_profile(host: str, username: str, path: Path = PROFILE_FILE) -> None:
    """一時ファイル→rename で保存する。パスワードは引数に取らない（保存できない作りにする）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({"host": host, "username": username}, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def classify_exit(exit_code: int) -> str:
    """読み取り役が終了したときの扱い。
    "ask_again": 資格情報の不備か、最初の 1 回が読めなかった → 自動で繰り返さず、ログイン画面を出し直す。
    "retry": それ以外の落ち方（強制終了など）→ 少し待って同じ資格情報で起動し直す。"""
    if exit_code in (EXIT_BAD_CREDENTIALS, EXIT_FIRST_READ_FAILED):
        return "ask_again"
    return "retry"
