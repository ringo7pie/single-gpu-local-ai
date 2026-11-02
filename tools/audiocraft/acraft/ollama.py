"""VRAM の空き確認だけを行う薄い道具（標準ライブラリのみ）。

hy3d.ollama の写し。Ollama のモデルを降ろす処理は入口ページ（tools/portal）が起動の前に行うので、
ここでは持たない。入口ページを通さず `run-audiocraft serve` を直接起動したときのために、
Ollama に載っているモデルの一覧だけ読めるようにしてある（診断メッセージ用）。
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any

from . import config

_CONNECT_TIMEOUT_SEC = 5.0


def loaded_models() -> list[dict[str, Any]]:
    """/api/ps に載っているモデルの一覧を返す。繋がらなければ空リスト。"""

    url = f"{config.OLLAMA_URL}/api/ps"
    try:
        with urllib.request.urlopen(url, timeout=_CONNECT_TIMEOUT_SEC) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return []

    result: list[dict[str, Any]] = []
    for model in payload.get("models") or []:
        name = model.get("name") or model.get("model")
        if name is None:
            continue
        result.append({"name": name, "size_vram": model.get("size_vram")})
    return result


def gpu_used_mib() -> int | None:
    """nvidia-smi から使用中 VRAM (MiB) を取得する。失敗したら None。"""

    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    output = completed.stdout.strip()
    if not output:
        return None
    try:
        return int(output.splitlines()[0].strip())
    except ValueError:
        return None


def wait_until_free(threshold_mib: int, timeout_seconds: float) -> bool:
    """VRAM 使用量が threshold_mib 未満になるまで 1 秒ごとに待つ。成否を bool で返す。

    nvidia-smi が失敗し続けた場合は、未満とは判断せず、タイムアウトまで待って False を返す。
    """

    deadline = time.monotonic() + timeout_seconds
    while True:
        used = gpu_used_mib()
        if used is not None and used < threshold_mib:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(1)
