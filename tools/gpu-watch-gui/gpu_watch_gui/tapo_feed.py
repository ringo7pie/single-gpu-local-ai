"""Tapo の読み取り役（tapo_power.py）を GPU Watch から使うための、GTK 非依存の部品。

パスワードはファイル・環境変数・コマンドライン引数に置かない。ログイン画面で受け取った値は GPU Watch の
メモリに置き、読み取り役の標準入力（1 行の JSON）で渡す。
ファイル（state/tapo.json）に残すのは IP とメールアドレスだけ（次回のログイン画面の初期値と、自動ログインの接続先。
パスワードは含めない）。
再起動後もログインを保つため、ログイン画面で「保存する」を選んだときに限り、パスワードをこの PC の TPM 2.0 に
封印して（暗号化して）state/tapo.cred に置く（gpu_watch_gui.tapo_vault。2026-11-03 のユーザー判断）。
"""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

GUI_DIR = Path(__file__).resolve().parent.parent
READER_SCRIPT = GUI_DIR / "tapo_power.py"
# `tapo`（PyPI）を入れた専用 venv。システムの python3 には入れない（setup-tapo.sh が作る）。
DEFAULT_PYTHON = Path.home() / ".local" / "share" / "tapo-meter" / ".venv" / "bin" / "python"
# 機種・IP・メールアドレスは機械ごとの値なので git 管理外の state/ に置く。
PROFILE_FILE = GUI_DIR / "state" / "tapo.json"
DEFAULT_INTERVAL_SEC = 5.0
# 保存分を復号して取った資格情報で、最初の 1 回が読めなかったときの試し直しの間隔（秒）。起動直後でネットワークが
# まだ使えない場合に備える。回数を絞るのは、誤ったパスワードで試し続けて機器側に制限されるのを避けるため。
# 使い切ったらログイン画面を出す（保存分は消さない）。
AUTO_LOGIN_RETRY_DELAYS_SEC = (30, 60, 120)

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


def auto_login_retry_delay(previous_failures: int) -> Optional[int]:
    """自動ログインが previous_failures 回失敗した後、次に試すまでの秒数。使い切ったら None。"""
    if 0 <= previous_failures < len(AUTO_LOGIN_RETRY_DELAYS_SEC):
        return AUTO_LOGIN_RETRY_DELAYS_SEC[previous_failures]
    return None


def classify_exit(exit_code: int) -> str:
    """読み取り役が終了したときの扱い。
    "ask_again": 資格情報の不備か、最初の 1 回が読めなかった → 自動で繰り返さず、ログイン画面を出し直す。
    "retry": それ以外の落ち方（強制終了など）→ 少し待って同じ資格情報で起動し直す。"""
    if exit_code in (EXIT_BAD_CREDENTIALS, EXIT_FIRST_READ_FAILED):
        return "ask_again"
    return "retry"
