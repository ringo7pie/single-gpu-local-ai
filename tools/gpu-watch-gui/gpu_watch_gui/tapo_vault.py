"""Tapo のログイン（TP-Link ID とパスワード）を、この PC の TPM 2.0 に封印して state/tapo.cred に置く。GTK 非依存。

再起動後もログインを保つために、パスワードを暗号化して保存する。人が何も入力せずに復号できる鍵は、
マシンに紐づく TPM にしか置けないので、`systemd-creds`（`--with-key=tpm2`）で封印する。
  - 暗号は AES-256-GCM。鍵は TPM が持ち、ディスクには残らない。この PC の TPM でしか復号できない。
  - 既定で PCR 7（Secure Boot の設定）にも縛られる。BIOS の設定やファームウェアを変えて復号できなくなったら、
    呼び出し側はログイン画面を出す（保存分は消さない。入力し直して保存すれば置き換わる）。
  - 守れるのは、ディスクのイメージ・ホームのコピー・バックアップからパスワードを読まれること。
    守れないのは、同じ利用者（mt）の他のプロセスが TPM に復号を頼むことと、Secure Boot が無効のこの PC で
    別の OS を起動して復号されること。

前提: mt が /dev/tpmrm0（所有者 tss:tss、0660）を開けること（`sudo usermod -aG tss mt` のあと再ログイン）。
開けないときは unavailable_reason() が理由を返し、保存は使えない（ログイン画面のチェックを無効にする）。

パスワードは systemd-creds の標準入力で渡し、コマンドライン引数・環境変数・一時ファイルには出さない。
封印するのは {"username", "password"} の JSON。ユーザー名も封印するのは、保存したパスワードと別のアカウントの
ユーザー名を組み合わせてログインしないため。呼び出しはすべて同期で、数百ミリ秒かかる。メインループから直接呼ばない
（app.py が別スレッドで呼ぶ）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, List, Optional, Tuple

GUI_DIR = Path(__file__).resolve().parent.parent
SEALED_FILE = GUI_DIR / "state" / "tapo.cred"  # git 管理外の state/ に置く（暗号文だけ。権限 600）
CREDENTIAL_NAME = "gpu-watch-tapo"  # 暗号文に埋め込む名前。別の用途に流用された暗号文を復号しないための印
TPM_DEVICE = Path("/dev/tpmrm0")
COMMAND_NAME = "systemd-creds"
COMMAND_TIMEOUT_SEC = 30.0
MAX_PROBLEM_CHARS = 200


class VaultError(Exception):
    """封印・復号に失敗した。メッセージは利用者に見せてよい文（入力の値を含めない）。"""


def unavailable_reason(device: Path = TPM_DEVICE, find_command: Callable[[str], Optional[str]] = shutil.which) -> Optional[str]:
    """封印を使えない理由。使えるなら None。"""
    if find_command(COMMAND_NAME) is None:
        return f"{COMMAND_NAME} がありません"
    if not os.access(device, os.R_OK | os.W_OK):
        return (f"TPM（{device}）を開けません。`sudo usermod -aG tss mt` を実行して、"
                "再ログイン（または再起動）してください")
    return None


def encrypt_argv(command_path: str) -> List[str]:
    """TPM だけで封印する。既定の host+tpm2 は root だけが読む鍵（/var/lib/systemd/credential.secret）も要るので使えない。"""
    return [command_path, "encrypt", "--with-key=tpm2", f"--name={CREDENTIAL_NAME}", "-", "-"]


def decrypt_argv(command_path: str) -> List[str]:
    return [command_path, "decrypt", f"--name={CREDENTIAL_NAME}", "-", "-"]


def encode_payload(username: str, password: str) -> bytes:
    return json.dumps({"username": username, "password": password}, ensure_ascii=False).encode("utf-8")


def decode_payload(data: bytes) -> Tuple[str, str]:
    try:
        parsed = json.loads(data.decode("utf-8"))
        username, password = parsed["username"], parsed["password"]
    except (ValueError, KeyError, TypeError):
        raise VaultError("復号した中身の形が違います") from None
    if not isinstance(username, str) or not isinstance(password, str) or not username or not password:
        raise VaultError("復号した中身にユーザー名かパスワードがありません")
    return username, password


def _command_path() -> str:
    path = shutil.which(COMMAND_NAME)
    if path is None:
        raise VaultError(f"{COMMAND_NAME} がありません")
    return path


def _run(argv: List[str], data: bytes, runner: Callable[..., "subprocess.CompletedProcess[bytes]"]) -> bytes:
    """systemd-creds を実行して標準出力を返す。失敗は VaultError。エラー文は最初の 1 行だけ（長さも切る）。"""
    try:
        completed = runner(argv, input=data, capture_output=True, timeout=COMMAND_TIMEOUT_SEC, check=False)
    except subprocess.TimeoutExpired:
        raise VaultError(f"{COMMAND_NAME} が {COMMAND_TIMEOUT_SEC:g} 秒で終わりませんでした") from None
    except OSError as error:
        raise VaultError(f"{COMMAND_NAME} を起動できません: {error}") from None
    if completed.returncode != 0:
        lines = completed.stderr.decode("utf-8", errors="replace").strip().splitlines()
        detail = lines[-1][:MAX_PROBLEM_CHARS] if lines else f"終了コード {completed.returncode}"
        raise VaultError(f"{COMMAND_NAME} が失敗しました: {detail}")
    return completed.stdout


def has_sealed(path: Path = SEALED_FILE) -> bool:
    return path.is_file()


def seal(username: str, password: str, path: Path = SEALED_FILE,
         runner: Callable[..., "subprocess.CompletedProcess[bytes]"] = subprocess.run) -> None:
    """封印して path に書く（一時ファイル→rename、権限 600）。書いた後に復号して一致を確かめ、
    合わなければファイルを消して VaultError（読み戻せない暗号文を残さない）。"""
    ciphertext = _run(encrypt_argv(_command_path()), encode_payload(username, password), runner)
    if not ciphertext.strip():
        raise VaultError(f"{COMMAND_NAME} の出力が空でした")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(ciphertext)
        os.replace(temporary, path)
    except OSError as error:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise VaultError(f"暗号文を書けません: {error}") from None
    try:
        read_back = unseal(path, runner)
    except VaultError as error:
        _discard(path)
        raise VaultError(f"封印後の確認で復号できませんでした: {error}") from None
    if read_back != (username, password):
        _discard(path)
        raise VaultError("封印後の確認で、復号した値が元と一致しませんでした")


def unseal(path: Path = SEALED_FILE,
           runner: Callable[..., "subprocess.CompletedProcess[bytes]"] = subprocess.run) -> Optional[Tuple[str, str]]:
    """保存したログインを復号して (ユーザー名, パスワード) を返す。保存が無ければ None。復号できなければ VaultError。"""
    try:
        ciphertext = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise VaultError(f"暗号文を読めません: {error}") from None
    return decode_payload(_run(decrypt_argv(_command_path()), ciphertext, runner))


def _discard(path: Path) -> None:
    try:
        clear(path)
    except OSError:
        pass  # 消せなくても、呼び出し側へ返す VaultError の方が大事


def clear(path: Path = SEALED_FILE) -> None:
    """保存したログインを消す（無くてもよい）。消せないときは OSError。"""
    try:
        path.unlink()
    except FileNotFoundError:
        pass
