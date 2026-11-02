#!/usr/bin/env python3
"""Tapo スマートプラグ（P110M）の消費電力(W)を読む。読み取り専用で、電源の ON/OFF は送らない。

GPU Watch の「PC 全体」を、推定ではなくコンセント側の実測で出すための読み取り役。
`tapo`（PyPI）は専用 venv にだけ入れる（setup-tapo.sh）。システムの python3 では動かない。
P110M(JP) の暗号方式は TPAP で、python-kasa は未対応。`tapo` 0.11.1 は対応している。

資格情報（TP-Link ID のメールアドレスとパスワード。Google のパスワードではない）は、ファイルにも
環境変数にもコマンドライン引数にも置かない。次のどちらかだけで受け取る:
  - 標準入力の 1 行目の JSON: {"host": "IP", "username": "メール", "password": "パスワード"}
    （GPU Watch がログイン画面で受け取り、子プロセスとしてこのスクリプトを起動して渡す）
  - 標準入力が端末のとき: 対話で尋ねる（パスワードは画面に出さない）

使い方（専用 venv の python で実行する）:
  ~/.local/share/tapo-meter/.venv/bin/python -I tapo_power.py [--host IP]        # 対話で 1 回読む
  ~/.local/share/tapo-meter/.venv/bin/python -I tapo_power.py --raw --interval 5  # GPU Watch 用

--raw の出力（gpu-watch.sh の raw 行と同じ「, 」区切り。取れなかったときは na）:
  TAPO, 2026/11/01 19:50:12.345, 196.4
終了コード: 0 正常 / 1 取得失敗（1 回モード）/ 3 資格情報の不備（入力が読めない・足りない）/
            4 最初の取得に失敗（--raw）。ログイン失敗か機器に届かないかは区別せず、同じ資格情報で
            自動で繰り返さない（誤ったパスワードの連続試行で機器側に制限されるのを避けるため）。
            最初に 1 回読めた後は、失敗しても na の行を出して続ける。
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import getpass
import json
import sys
from typing import Optional, TextIO, Tuple

DEFAULT_INTERVAL_SEC = 5.0
MIN_INTERVAL_SEC = 2.0  # 機器側の制限は未確認。2 秒間隔で 40 秒続けて問題が出なかった範囲を下限にする
DISCOVERY_TIMEOUT_SEC = 6
CONNECTION_TIMEOUT_SEC = 10
RETRY_DELAY_SEC = 5.0
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BAD_CREDENTIALS = 3
EXIT_FIRST_READ_FAILED = 4


class CredentialsError(Exception):
    """資格情報が読めない・足りない。再試行しても直らない。"""


def parse_credentials_line(line: str) -> Tuple[str, str, str]:
    """標準入力 1 行目の JSON から (IP, ユーザー名, パスワード) を取り出す。足りなければ CredentialsError。"""
    try:
        data = json.loads(line)
    except ValueError as error:
        raise CredentialsError(f"資格情報の JSON が読めません: {error.__class__.__name__}") from None
    if not isinstance(data, dict):
        raise CredentialsError("資格情報は JSON オブジェクトで渡してください")
    host = str(data.get("host", "")).strip()
    username = str(data.get("username", ""))
    password = str(data.get("password", ""))
    if not host:
        raise CredentialsError("host（プラグの IP）がありません")
    if not username or not password:
        raise CredentialsError("username / password がありません")
    return host, username, password


def read_credentials(stdin: TextIO, default_host: str) -> Tuple[str, str, str]:
    if stdin.isatty():
        host = default_host or input("プラグの IP: ").strip()
        username = input("TP-Link ID（メールアドレス）: ").strip()
        password = getpass.getpass("パスワード: ")
        return parse_credentials_line(json.dumps({"host": host, "username": username, "password": password}))
    line = stdin.readline()
    if not line.strip():
        raise CredentialsError("標準入力から資格情報を受け取れませんでした")
    host, username, password = parse_credentials_line(line)
    return (default_host or host), username, password


def format_stamp(moment: datetime.datetime) -> str:
    """gpu-watch.sh の時刻表記 "2026/10/02 10:40:25.135" に揃える。"""
    return moment.strftime("%Y/%m/%d %H:%M:%S.") + f"{moment.microsecond // 1000:03d}"


def format_raw_line(moment: datetime.datetime, watts: Optional[float]) -> str:
    value = "na" if watts is None else f"{watts:.1f}"
    return f"TAPO, {format_stamp(moment)}, {value}"


async def locate(client, address: str):
    results = await client.discover_devices(address, DISCOVERY_TIMEOUT_SEC)
    async for maybe_result in results:
        found = maybe_result.get()
        if found.ip == address:
            return found
    raise LookupError(f"{address} から応答がありませんでした")


async def read_power_watts(found) -> float:
    """current_power は mW。取れなければ get_current_power（W）に落とす。"""
    usage = await found.handler.get_energy_usage()
    milliwatts = getattr(usage, "current_power", None)
    if milliwatts is not None:
        return milliwatts / 1000.0
    return float((await found.handler.get_current_power()).current_power)


async def run_once(host: str, username: str, password: str) -> int:
    from tapo import ApiClient  # 専用 venv にだけある。試験で import できるよう遅延させる

    client = ApiClient(username, password, CONNECTION_TIMEOUT_SEC)
    found = await locate(client, host)
    watts = await read_power_watts(found)
    print(f"{datetime.datetime.now():%H:%M:%S}  {host}  {watts:.1f} W")
    return EXIT_OK


async def run_raw(host: str, username: str, password: str, interval_sec: float) -> int:
    """最初の 1 回が失敗したら終了する。読めた後は、失敗しても na の行を出して機器を探し直しながら続ける。"""
    from tapo import ApiClient

    client = ApiClient(username, password, CONNECTION_TIMEOUT_SEC)
    found = None
    has_read_once = False
    while True:
        watts: Optional[float] = None
        try:
            if found is None:
                found = await locate(client, host)
            watts = await read_power_watts(found)
            has_read_once = True
        except Exception as error:  # tapo は Rust 側の例外型を投げるため、型を絞らない
            print(f"Tapo の取得に失敗: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
            if not has_read_once:
                return EXIT_FIRST_READ_FAILED
            found = None
        print(format_raw_line(datetime.datetime.now(), watts), flush=True)
        await asyncio.sleep(interval_sec if watts is not None else max(interval_sec, RETRY_DELAY_SEC))


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw", action="store_true", help="GPU Watch 用。TAPO 行を出し続ける")
    parser.add_argument("--host", default="", help="プラグの IP。省略すると標準入力の JSON の host")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SEC, metavar="SEC",
                        help=f"--raw の読み取り間隔（秒）。既定 {DEFAULT_INTERVAL_SEC:g}、下限 {MIN_INTERVAL_SEC:g}")
    args = parser.parse_args(argv)
    if args.interval < MIN_INTERVAL_SEC:
        parser.error(f"--interval は {MIN_INTERVAL_SEC:g} 以上にしてください: {args.interval}")
    try:
        host, username, password = read_credentials(sys.stdin, args.host)
    except CredentialsError as error:
        print(f"tapo_power.py: {error}", file=sys.stderr)
        return EXIT_BAD_CREDENTIALS
    try:
        if args.raw:
            return asyncio.run(run_raw(host, username, password, args.interval))
        return asyncio.run(run_once(host, username, password))
    except KeyboardInterrupt:
        return EXIT_OK
    except Exception as error:  # tapo は Rust 側の例外型を投げるため、型を絞らない
        print(f"tapo_power.py: 取得に失敗しました: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
