#!/usr/bin/env python3
"""localai-mcp — 他の PC の Claude Code / Codex から、A30 サーバーの入口ページ（8080）の AI を使う MCP サーバー。

他の PC で stdio の MCP サーバーとして動き、LAN 越しにサーバーの各ポートへ HTTP で話す。Python 3.10 以上の
標準ライブラリだけで動く（依存ゼロ）。入力（画像・音）と出力（wav・glb・画像・動画）は、この MCP を動かしている
PC のローカルファイルとして扱う。

環境変数:
    LOCALAI_SERVER       サーバーのアドレス（例 192.168.1.10 か http://192.168.1.10）。必須
    LOCALAI_PORTAL_PORT  入口ページのポート。既定 8080
    LOCALAI_OUTPUT_DIR   出力の保存先。既定は起動時の作業フォルダの localai-outputs/

起動と停止は入口ページの API（GET /api/status、POST /api/service/<key>/<start|stop>?wait=<秒>）に任せる。
Ollama のモデルの読み込み・解放も入口ページ（GET /api/ollama、POST /api/ollama/<model>/<load|unload>）を通す。GPU の排他
（ほかのサービスを止める）と Ollama のモデルを降ろす処理は入口ページが行う。この MCP は、それで他の人の作業を
黙って止めないよう、止まるもの・降ろされるものがあるときは stop_others=true が無い限り起動しない。

自己点検: python3 localai_mcp.py --check   （サーバーへの到達と各ポートの開き具合を表示して終わる）
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

SERVER_NAME = "localai"
SERVER_VERSION = "0.3.0"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

DEFAULT_PORTAL_PORT = 8080

# 入口ページ（tools/portal/portal.py）の GPU_EXCLUSIVE_KEYS・OLLAMA_UNLOAD_BEFORE_START_KEYS と同じ。
# /api/status はこの一覧を返さないので写しを持つ。tests/test_units.py が portal.py と一致するかを確かめる。
GPU_EXCLUSIVE_KEYS = ("hunyuan3d", "pixal3d", "comfyui", "strata", "strata-coder", "strata-official",
                      "strata-official-iq3s", "strata-swift", "audiocraft", "qwen3-tts", "stable-audio")
OLLAMA_UNLOAD_BEFORE_START_KEYS = ("strata", "strata-coder", "strata-official", "strata-official-iq3s", "strata-swift",
                                   "audiocraft", "qwen3-tts", "stable-audio")

STRATA_KEYS = ("strata", "strata-coder", "strata-official", "strata-official-iq3s", "strata-swift")
GRADIO_KEYS = ("hunyuan3d", "pixal3d", "audiocraft", "qwen3-tts", "stable-audio")

# 3D 生成の画像の引数名（Gradio の parameter_name）と、探す api_name の候補。
# Hunyuan3D は上流 gradio_app.py の shape_generation / generation_all、Pixal3D は px3d/ui.py の _generate。
MESH_ENGINES: dict[str, dict[str, Any]] = {
    "hunyuan3d": {
        "views": {"front": "mv_image_front", "back": "mv_image_back", "left": "mv_image_left", "right": "mv_image_right"},
        "endpoints": {False: ("/shape_generation",), True: ("/generation_all",)},
    },
    "pixal3d": {
        "views": {"front": "front_image", "back": "back_image", "left": "left_image", "right": "right_image"},
        "endpoints": {False: ("/_generate", "/generate"), True: ("/_generate", "/generate")},
    },
}

HTTP_TIMEOUT_SHORT = 10.0
# 入口ページの ?wait= の上限（portal.py の WAIT_MAX_SEC と同じ）。これを超える待ちは、残りを MCP 側の問い合わせで待つ。
PORTAL_WAIT_MAX_SEC = 1800
# HTTP の待ちは、入口ページが待つ秒数にこの余裕を足す。
PORTAL_WAIT_MARGIN_SEC = 60.0
# Ollama のモデルの読み込みの待ち（入口ページ側の上限 300 秒 + 停止の待ちの余裕）。
OLLAMA_LOAD_HTTP_TIMEOUT_SEC = 600.0
SERVICE_POLL_SEC = 5.0
COMFY_POLL_SEC = 3.0
RESULT_TEXT_LIMIT = 4000
RESULT_LIST_LIMIT = 10

FILE_COMPONENTS = {"image", "file", "audio", "video", "model3d", "gallery", "uploadbutton"}


class ToolError(Exception):
    """ツールの失敗。message は利用者（モデル）にそのまま見せる日本語。"""


# ----------------------------------------------------------------------------------------------------------------
# 設定

def server_host() -> str:
    raw = os.environ.get("LOCALAI_SERVER", "").strip()
    if not raw:
        raise ToolError("環境変数 LOCALAI_SERVER が空です。サーバーのアドレス（例 192.168.1.10）を MCP の登録に入れてください。")
    if "://" in raw:
        raw = urllib.parse.urlsplit(raw).hostname or ""
    raw = raw.strip().strip("/")
    if raw.count(":") == 1:  # host:port を渡されたときは host だけ使う（ポートはサービスごとに決まる）
        raw = raw.split(":", 1)[0]
    if not raw:
        raise ToolError("LOCALAI_SERVER からホスト名を読めません。")
    return raw


def portal_port() -> int:
    raw = os.environ.get("LOCALAI_PORTAL_PORT", "").strip()
    return int(raw) if raw else DEFAULT_PORTAL_PORT


def base_url(port: int) -> str:
    return f"http://{server_host()}:{port}"


def portal_url() -> str:
    return base_url(portal_port())


def output_dir(override: str | None = None) -> Path:
    raw = override or os.environ.get("LOCALAI_OUTPUT_DIR", "").strip()
    path = Path(raw).expanduser() if raw else Path.cwd() / "localai-outputs"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def resolve_input_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.is_file():
        raise ToolError(f"入力ファイルがありません: {path}")
    return path.resolve()


SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def unique_output_path(directory: Path, stem: str, suffix: str) -> Path:
    """`<時刻>_<stem><suffix>` を作る。同名があれば -2, -3 … を付ける（上書きしない）。"""
    safe_stem = SAFE_NAME.sub("_", stem).strip("._") or "output"
    safe_stem = safe_stem[:80]
    if suffix and not suffix.startswith("."):
        suffix = "." + suffix
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = directory / f"{stamp}_{safe_stem}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = directory / f"{stamp}_{safe_stem}-{counter}{suffix}"
        counter += 1
    return candidate


def save_bytes(data: bytes, stem: str, suffix: str, directory: str | None = None) -> str:
    path = unique_output_path(output_dir(directory), stem, suffix)
    path.write_bytes(data)
    return str(path)


# ----------------------------------------------------------------------------------------------------------------
# HTTP

class HttpResponse:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8")) if self.body else None
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ToolError(f"JSON として読めない応答です（HTTP {self.status}）: {self.body[:300]!r}") from error

    def error_text(self) -> str:
        try:
            parsed = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self.body.decode("utf-8", "replace")[:1000]
        if isinstance(parsed, dict):
            for key in ("error", "message", "detail"):
                if key in parsed:
                    value = parsed[key]
                    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)[:1000]
        return json.dumps(parsed, ensure_ascii=False)[:1000]


def http_request(
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = HTTP_TIMEOUT_SHORT,
) -> HttpResponse:
    """HTTP を 1 回送る。4xx / 5xx も例外にせず HttpResponse で返す。届かないときだけ ToolError。"""
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HttpResponse(response.status, dict(response.headers.items()), response.read())
    except urllib.error.HTTPError as error:
        return HttpResponse(error.code, dict(error.headers.items()) if error.headers else {}, error.read() or b"")
    except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, OSError) as error:
        reason = getattr(error, "reason", error)
        raise ToolError(
            f"{url} に届きません（{reason}）。サービスが起動しているか、サーバーの ufw でポートが LAN に開いているかを確かめてください"
            "（localai_status と、README の「サーバー側の準備」）。"
        ) from error


def http_json(method: str, url: str, payload: Any = None, *, headers: dict[str, str] | None = None,
              timeout: float = HTTP_TIMEOUT_SHORT) -> HttpResponse:
    all_headers = {"Accept": "application/json"}
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        all_headers["Content-Type"] = "application/json"
    all_headers.update(headers or {})
    return http_request(method, url, body=body, headers=all_headers, timeout=timeout)


def build_multipart(fields: list[tuple[str, str]], files: list[tuple[str, str, bytes]]) -> tuple[bytes, str]:
    """multipart/form-data の本文と Content-Type を作る（純粋関数）。files は (フィールド名, ファイル名, 中身)。"""
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields:
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8")
        )
    for name, filename, content in files:
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        safe_filename = filename.replace("\"", "_").replace("\r", "_").replace("\n", "_")
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{safe_filename}\"\r\n"
            f"Content-Type: {content_type}\r\n\r\n".encode("utf-8")
        )
        parts.append(content)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


# ----------------------------------------------------------------------------------------------------------------
# 入口ページ

def fetch_status() -> dict[str, Any]:
    response = http_json("GET", f"{portal_url()}/api/status")
    if response.status != 200:
        raise ToolError(f"入口ページの /api/status が HTTP {response.status} を返しました: {response.error_text()}")
    status = response.json()
    if not isinstance(status, dict) or not isinstance(status.get("services"), list):
        raise ToolError("入口ページの /api/status の形が想定と違います。")
    return status


def services_by_key(status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {service["key"]: service for service in status["services"] if isinstance(service, dict) and "key" in service}


def find_service(status: dict[str, Any], key: str) -> dict[str, Any]:
    service = services_by_key(status).get(key)
    if service is None:
        known = ", ".join(services_by_key(status))
        raise ToolError(f"サービス {key!r} は入口ページにありません。使える key: {known}")
    return service


def require_running(key: str) -> dict[str, Any]:
    """サービスが稼働中（ポートが開いている）ことを確かめ、その 1 件を返す。止まっていれば ToolError。"""
    service = find_service(fetch_status(), key)
    state = service.get("state")
    if state != "running":
        raise ToolError(
            f"{service.get('name', key)}（{key}）は {state} です。先に localai_service で key={key!r}, action='start' を実行してください。"
        )
    return service


def summarize_status(status: dict[str, Any]) -> dict[str, Any]:
    gpu = status.get("gpu") or {}
    ollama = status.get("ollama") or {}
    return {
        "server": server_host(),
        "gpu": {"used_mib": gpu.get("used_mib"), "total_mib": gpu.get("total_mib"), "available": gpu.get("available")},
        "ollama_models_loaded": [model.get("name") for model in (ollama.get("models") or []) if isinstance(model, dict)],
        "services": [
            {
                "key": service.get("key"),
                "name": service.get("name"),
                "port": service.get("port"),
                "state": service.get("state"),
                "gpu_exclusive": service.get("key") in GPU_EXCLUSIVE_KEYS,
            }
            for service in status["services"]
        ],
        "note": "state: running=使える / starting=起動中 / stopped=停止 / not_installed=未導入。GPU を使うもの（gpu_exclusive）は同時に 1 つだけ動く。",
    }


def start_side_effects(key: str, status: dict[str, Any]) -> dict[str, Any]:
    """key を起動すると入口ページが止めるサービスと、降ろす Ollama のモデルを求める（純粋関数）。"""
    services = services_by_key(status)
    target_state = (services.get(key) or {}).get("state")
    to_stop: list[str] = []
    if key in GPU_EXCLUSIVE_KEYS:
        to_stop = [
            other for other in GPU_EXCLUSIVE_KEYS
            if other != key and (services.get(other) or {}).get("state") in ("running", "starting")
        ]
    to_unload: list[str] = []
    if key in OLLAMA_UNLOAD_BEFORE_START_KEYS and target_state == "stopped":
        to_unload = [model.get("name") for model in ((status.get("ollama") or {}).get("models") or [])
                     if isinstance(model, dict) and model.get("name")]
    return {"stop_services": to_stop, "unload_ollama_models": to_unload}


def wait_for_state(key: str, wanted: str, timeout_sec: float) -> tuple[str, float]:
    started = time.monotonic()
    state = "unknown"
    while True:
        try:
            state = find_service(fetch_status(), key).get("state", "unknown")
        except ToolError:
            state = "unknown"
        elapsed = time.monotonic() - started
        if state == wanted:
            return state, elapsed
        if wanted == "running" and state == "stopped" and elapsed > 30:
            # 起動を頼んだのに 30 秒たっても stopped のままなら、起動に失敗している（ユニットが落ちた）。
            return state, elapsed
        if elapsed >= timeout_sec:
            return state, elapsed
        time.sleep(SERVICE_POLL_SEC)


def is_old_portal_wait_error(response: HttpResponse) -> bool:
    """?wait= を知らない古い入口ページの 400（`start?wait=…` を action とみなして断る）かどうか。"""
    return response.status == 400 and "action は" in response.error_text()


def running_gpu_services(status: dict[str, Any]) -> list[str]:
    """GPU を排他的に使うサービスのうち、動いている（running / starting）ものの key（純粋関数）。"""
    services = services_by_key(status)
    return [key for key in GPU_EXCLUSIVE_KEYS if (services.get(key) or {}).get("state") in ("running", "starting")]


def portal_ollama_json(response: HttpResponse) -> dict[str, Any]:
    """入口ページの /api/ollama の応答を辞書にする。/api/ollama を持たない古い入口ページなら ToolError。"""
    try:
        result = response.json()
    except ToolError:
        result = None
    # 古い入口ページは /api/ollama を知らず、HTML か {"ok", "message"} の無い 404 / 405 を返す。
    known_shape = isinstance(result, dict) and any(key in result for key in ("ok", "installed", "reachable"))
    if not known_shape:
        if response.status in (404, 405):
            raise ToolError(
                "入口ページが古く、Ollama の操作（/api/ollama）がありません。サーバーで `systemctl --user restart portal` を"
                "実行してもらうか、新しい tools/portal/portal.py に更新してください。"
            )
        raise ToolError(f"入口ページの /api/ollama の応答を読めません（HTTP {response.status}）: {response.error_text()}")
    return result


def tool_ollama(args: dict[str, Any]) -> dict[str, Any]:
    action = str(args.get("action") or "list").strip()
    if action not in ("list", "load", "unload"):
        raise ToolError("action は 'list'・'load'・'unload' のどれかです。")
    if action == "list":
        response = http_json("GET", f"{portal_url()}/api/ollama")
        result = portal_ollama_json(response)
        if response.status != 200:
            raise ToolError(f"Ollama の一覧を取れません（HTTP {response.status}）: {result.get('message') or response.error_text()}")
        return result

    model = str(args.get("model") or "").strip()
    if not model or any(character.isspace() for character in model):
        raise ToolError("model に Ollama のモデル名（例 qwen3.8:27b）を入れてください。action='list' で一覧が出ます。")
    query = ""
    if action == "load":
        keep_alive = args.get("keep_alive")
        if keep_alive not in (None, ""):
            query = "?" + urllib.parse.urlencode({"keep_alive": str(keep_alive)})
        status = fetch_status()
        to_stop = running_gpu_services(status)
        if to_stop and not bool(args.get("stop_others", False)):
            names = {item["key"]: item.get("name") for item in status["services"]}
            return {
                "ok": False,
                "model": model,
                "would_stop": [{"key": key, "name": names.get(key)} for key in to_stop],
                "message": (
                    "読み込むと would_stop のサービスが止まります（Qwen 27B は VRAM を 22 GiB 使い、GPU を取り合うため）。"
                    "他の人の作業を落とさないよう、ここでは読み込んでいません。止めてよいなら stop_others=true で呼び直してください。"
                ),
            }
    timeout = float(args.get("timeout_sec") or (OLLAMA_LOAD_HTTP_TIMEOUT_SEC if action == "load" else 60.0))
    response = http_json(
        "POST", f"{portal_url()}/api/ollama/{urllib.parse.quote(model, safe=':/')}/{action}{query}",
        payload=None, headers={"X-Portal-Request": "1"}, timeout=timeout,
    )
    result = portal_ollama_json(response)
    if response.status == 404 and "installed" in result:
        raise ToolError(f"{result.get('message')} 入っているモデル: {', '.join(result['installed'])}")
    if response.status != 200:
        raise ToolError(f"Ollama の {action} に失敗しました（HTTP {response.status}）: {result.get('message') or response.error_text()}")
    if action == "load" and result.get("ok"):
        result["note"] = (
            "Ollama（11434）は LAN に公開していないので、この MCP からは話せません。"
            f"Open WebUI（http://{server_host()}:8081/）か、SSH 経由の qwen-worker で使ってください。"
        )
    return result


def tool_status(_: dict[str, Any]) -> dict[str, Any]:
    return summarize_status(fetch_status())


def tool_service(args: dict[str, Any]) -> dict[str, Any]:
    key = str(args.get("key") or "").strip()
    action = str(args.get("action") or "").strip()
    if action not in ("start", "stop"):
        raise ToolError("action は 'start' か 'stop' です。")
    if key == "portal":
        raise ToolError("入口ページ（portal）自身はこの MCP からは操作できません（止めると戻せなくなるため）。")
    wait = bool(args.get("wait", True))
    stop_others = bool(args.get("stop_others", False))
    timeout_sec = float(args.get("timeout_sec") or 900)

    status = fetch_status()
    service = find_service(status, key)
    before = service.get("state")
    name = service.get("name", key)
    if before == "not_installed":
        raise ToolError(f"{name} はサーバーに導入されていません。")

    if action == "start":
        if before == "running":
            return {"ok": True, "key": key, "state": "running", "message": f"{name} は既に稼働中です。"}
        effects = start_side_effects(key, status)
        if (effects["stop_services"] or effects["unload_ollama_models"]) and not stop_others:
            names = {item["key"]: item.get("name") for item in status["services"]}
            return {
                "ok": False,
                "key": key,
                "state": before,
                "would_stop": [{"key": other, "name": names.get(other)} for other in effects["stop_services"]],
                "would_unload_ollama_models": effects["unload_ollama_models"],
                "message": (
                    "起動すると、" + "、".join(
                        part for part in (
                            "would_stop のサービスが止まり" if effects["stop_services"] else "",
                            "would_unload_ollama_models のモデルが Ollama から降ろされ" if effects["unload_ollama_models"] else "",
                        ) if part
                    ) + "ます（GPU は 1 枚を取り合うため）。他の人の作業を落とさないよう、ここでは起動していません。"
                    "止めてよいなら stop_others=true で呼び直してください。"
                ),
            }
    elif before == "stopped":
        return {"ok": True, "key": key, "state": "stopped", "message": f"{name} は既に停止しています。"}

    action_url = f"{portal_url()}/api/service/{urllib.parse.quote(key)}/{action}"
    started = time.monotonic()
    server_wait = int(min(max(timeout_sec, 0), PORTAL_WAIT_MAX_SEC)) if (action == "start" and wait) else None
    if server_wait is not None:
        # 入口ページに、使える状態（ポートが応答する）になるまで待ってもらう。起動失敗も入口ページが見分ける。
        response = http_json(
            "POST", f"{action_url}?wait={server_wait}", payload=None, headers={"X-Portal-Request": "1"},
            timeout=server_wait + PORTAL_WAIT_MARGIN_SEC,
        )
        if response.status == 400 and is_old_portal_wait_error(response):
            # ?wait= を知らない古い入口ページ。待たずに起動を頼み、下で MCP 側から問い合わせて待つ。
            server_wait = None
            response = http_json("POST", action_url, payload=None, headers={"X-Portal-Request": "1"}, timeout=120.0)
    else:
        response = http_json("POST", action_url, payload=None, headers={"X-Portal-Request": "1"}, timeout=120.0)
    try:
        result = response.json() or {}
    except ToolError:
        result = {"message": response.error_text()}
    if response.status != 200 or (not result.get("ok", False) and "ready" not in result):
        raise ToolError(f"{name} の {action} に失敗しました（HTTP {response.status}）: {result.get('message') or response.error_text()}")

    output: dict[str, Any] = {
        "ok": bool(result.get("ok", False)), "key": key, "action": action, "message": result.get("message"),
        "stopped": result.get("stopped", []), "unloaded_models": result.get("unloaded_models", []),
    }
    if not wait:
        output["state"] = "starting" if action == "start" else "stopping"
        output["note"] = "wait=false なので待っていません。localai_status で state を確かめてください。"
        return output
    wanted = "running" if action == "start" else "stopped"
    if server_wait is not None and "ready" in result:
        state = str(result.get("state") or "unknown")
        elapsed = time.monotonic() - started
        if not result.get("ready") and result.get("ok") and timeout_sec > elapsed + 1:
            # 入口ページの上限（30 分）では足りなかった。残りは MCP 側で待つ。
            state, extra = wait_for_state(key, wanted, timeout_sec - elapsed)
            elapsed += extra
        output["state"] = state
        output["waited_sec"] = round(elapsed, 1)
        if not result.get("ok"):
            # 入口ページが起動失敗（起動から 10 秒以上たっても停止中）と判断した。message に journalctl の案内が入っている。
            return output
    else:
        state, elapsed = wait_for_state(key, wanted, timeout_sec)
        output["state"] = state
        output["waited_sec"] = round(elapsed, 1)
    if state != wanted:
        output["ok"] = False
        output["message"] = (
            f"{name} は {round(elapsed)} 秒たっても {wanted} になっていません（いまは {state}）。"
            "Strata・Hunyuan3D は読み込みに数分かかることがあります。localai_status で様子を見てください。"
        )
    return output


# ----------------------------------------------------------------------------------------------------------------
# Strata（OpenAI 互換）

def tool_chat(args: dict[str, Any]) -> dict[str, Any]:
    key = str(args.get("model") or "strata").strip()
    if key not in STRATA_KEYS:
        raise ToolError(f"model は {', '.join(STRATA_KEYS)} のどれかです。")
    service = require_running(key)
    url = base_url(int(service["port"]))

    messages = args.get("messages")
    if messages is None:
        prompt = args.get("prompt")
        if not prompt:
            raise ToolError("prompt か messages のどちらかが要ります。")
        messages = [{"role": "user", "content": str(prompt)}]
    if not isinstance(messages, list) or not messages:
        raise ToolError("messages は [{role, content}, ...] の配列です。")
    if args.get("system"):
        messages = [{"role": "system", "content": str(args["system"])}] + list(messages)

    models = http_json("GET", f"{url}/v1/models", timeout=30.0)
    model_name = key
    if models.status == 200:
        data = (models.json() or {}).get("data") or []
        if data and isinstance(data[0], dict) and data[0].get("id"):
            model_name = data[0]["id"]

    body: dict[str, Any] = {"model": model_name, "messages": messages, "stream": False}
    for field in ("max_tokens", "temperature", "top_p"):
        if args.get(field) is not None:
            body[field] = args[field]
    extra = args.get("extra") or {}
    if not isinstance(extra, dict):
        raise ToolError("extra は辞書です（OpenAI 互換 API の本文にそのまま足す項目）。")
    body.update(extra)

    timeout = float(args.get("timeout_sec") or 1800)
    started = time.monotonic()
    response = http_json("POST", f"{url}/v1/chat/completions", payload=body, timeout=timeout)
    elapsed = time.monotonic() - started
    if response.status != 200:
        raise ToolError(f"Strata が HTTP {response.status} を返しました: {response.error_text()}")
    result = response.json() or {}
    choice = (result.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    output: dict[str, Any] = {
        "model": result.get("model", model_name),
        "content": message.get("content"),
        "finish_reason": choice.get("finish_reason"),
        "usage": result.get("usage"),
        "elapsed_sec": round(elapsed, 1),
        "reasoning_chars": len(reasoning),
    }
    if args.get("include_reasoning"):
        output["reasoning"] = reasoning
    if args.get("save_to_file"):
        output["saved"] = save_bytes(
            json.dumps({"request": body, "response": result}, ensure_ascii=False, indent=2).encode("utf-8"),
            f"chat_{key}", ".json", args.get("output_dir"),
        )
    return output


# ----------------------------------------------------------------------------------------------------------------
# REST の音（AudioCraft・Stable Audio）

def post_audio_rest(key: str, path: str, body: dict[str, Any], header_prefix: str, stem: str,
                    directory: str | None, timeout: float) -> dict[str, Any]:
    service = require_running(key)
    url = f"{base_url(int(service['port']))}{path}"
    started = time.monotonic()
    response = http_json("POST", url, payload=body, timeout=timeout)
    elapsed = time.monotonic() - started
    if response.status != 200:
        hint = "（混雑。30 秒ほど待ってから呼び直してください）" if response.status == 503 else ""
        raise ToolError(f"{key} が HTTP {response.status} を返しました{hint}: {response.error_text()}")
    saved = save_bytes(response.body, stem, ".wav", directory)
    info = {
        name[len(header_prefix):].lower().replace("-", "_"): value
        for name, value in response.headers.items() if name.lower().startswith(header_prefix.lower())
    }
    return {"saved": saved, "bytes": len(response.body), "elapsed_sec": round(elapsed, 1), "server_info": info}


def audio_body(args: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    body = {field: args[field] for field in fields if args.get(field) is not None}
    if not body.get("prompt"):
        raise ToolError("prompt（英語の説明文）が要ります。")
    if args.get("audio_path"):
        body["audio_base64"] = base64.b64encode(resolve_input_path(str(args["audio_path"])).read_bytes()).decode("ascii")
    return body


def tool_music(args: dict[str, Any]) -> dict[str, Any]:
    body = audio_body(args, ("prompt", "duration", "seed", "top_k", "top_p", "temperature", "cfg_coef", "mode", "keep"))
    return post_audio_rest("audiocraft", "/api/v1/music", body, "X-Acraft-", "music",
                           args.get("output_dir"), float(args.get("timeout_sec") or 600))


def tool_sound(args: dict[str, Any]) -> dict[str, Any]:
    body = audio_body(args, ("prompt", "duration", "seed", "top_k", "top_p", "temperature", "cfg_coef", "mode", "keep"))
    return post_audio_rest("audiocraft", "/api/v1/sound", body, "X-Acraft-", "sound",
                           args.get("output_dir"), float(args.get("timeout_sec") or 600))


def tool_stable_audio(args: dict[str, Any]) -> dict[str, Any]:
    body = audio_body(args, ("prompt", "duration", "seed", "steps", "mode", "init_noise_level",
                             "inpaint_start", "inpaint_end", "keep"))
    return post_audio_rest("stable-audio", "/api/v1/generate", body, "X-Saudio-", "stable_audio",
                           args.get("output_dir"), float(args.get("timeout_sec") or 900))


# ----------------------------------------------------------------------------------------------------------------
# Gradio（Qwen3-TTS・Hunyuan3D・Pixal3D、ほかの Gradio の口も汎用ツールで）

def gradio_info(base: str) -> dict[str, Any]:
    response = http_json("GET", f"{base}/gradio_api/info", timeout=30.0)
    if response.status != 200:
        raise ToolError(f"{base}/gradio_api/info が HTTP {response.status} を返しました。")
    info = response.json() or {}
    if not isinstance(info.get("named_endpoints"), dict):
        raise ToolError("Gradio の API 情報の形が想定と違います。")
    return info


def is_file_parameter(parameter: dict[str, Any]) -> bool:
    component = str(parameter.get("component") or "").lower()
    if component in FILE_COMPONENTS:
        return True
    return "FileData" in json.dumps(parameter.get("type") or {})


def gradio_upload(base: str, path: Path) -> dict[str, Any]:
    body, content_type = build_multipart([], [("files", path.name, path.read_bytes())])
    response = http_request("POST", f"{base}/gradio_api/upload", body=body,
                            headers={"Content-Type": content_type}, timeout=300.0)
    if response.status != 200:
        raise ToolError(f"Gradio へのアップロードが HTTP {response.status} で失敗しました: {response.error_text()}")
    uploaded = response.json()
    if not isinstance(uploaded, list) or not uploaded:
        raise ToolError("Gradio のアップロードの応答が想定と違います。")
    return {"path": uploaded[0], "orig_name": path.name, "meta": {"_type": "gradio.FileData"}}


def build_gradio_data(parameters: list[dict[str, Any]], values: dict[str, Any]) -> list[Any]:
    """endpoint の parameters の順に、渡された値か既定値を並べる（純粋関数）。未知の名前は ToolError。"""
    names = [str(parameter.get("parameter_name")) for parameter in parameters]
    unknown = sorted(set(values) - set(names))
    if unknown:
        raise ToolError(f"この API に無い引数です: {', '.join(unknown)}。使える引数: {', '.join(names)}")
    data = []
    for parameter, name in zip(parameters, names):
        if name in values:
            data.append(values[name])
        elif parameter.get("parameter_has_default"):
            data.append(parameter.get("parameter_default"))
        else:
            data.append(None)
    return data


def parse_sse(lines: Any) -> tuple[str, Any]:
    """Gradio の /gradio_api/call/<api>/<id> の SSE から、最後の complete か error を取り出す（純粋関数）。

    `lines` はバイト列の行の反復子。戻り値は ("complete", data) か ("error", data)。
    """
    event = None
    for raw in lines:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
            continue
        if line.startswith("data:"):
            payload = line[len("data:"):].strip()
            if event in ("complete", "error"):
                try:
                    parsed = json.loads(payload) if payload else None
                except json.JSONDecodeError:
                    parsed = payload
                return event, parsed
    raise ToolError("Gradio の結果が届く前に接続が切れました（生成に失敗したか、サーバーが再起動した可能性）。")


def gradio_call(base: str, api_name: str, data: list[Any], timeout: float) -> Any:
    name = api_name.lstrip("/")
    response = http_json("POST", f"{base}/gradio_api/call/{name}", payload={"data": data}, timeout=60.0)
    if response.status != 200:
        raise ToolError(f"Gradio の呼び出し /{name} が HTTP {response.status} で失敗しました: {response.error_text()}")
    event_id = (response.json() or {}).get("event_id")
    if not event_id:
        raise ToolError("Gradio が event_id を返しませんでした。")
    request = urllib.request.Request(f"{base}/gradio_api/call/{name}/{event_id}", headers={"Accept": "text/event-stream"})
    try:
        # 1 回の読み取りの待ちを timeout にする。Gradio は処理中も数秒おきに heartbeat を送る。
        with urllib.request.urlopen(request, timeout=timeout) as stream:
            event, payload = parse_sse(stream)
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as error:
        raise ToolError(f"Gradio の結果を待つ間に失敗しました: {error}") from error
    if event == "error":
        detail = payload if payload is not None else "（理由は返されませんでした。サーバーのログを確かめてください）"
        raise ToolError(f"Gradio の処理が失敗しました: {detail}")
    return payload


def collect_file_data(value: Any) -> list[dict[str, Any]]:
    """結果の中の FileData（path か url を持つ辞書）を順に集める（純粋関数）。"""
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        meta_type = (value.get("meta") or {}).get("_type") if isinstance(value.get("meta"), dict) else None
        if meta_type == "gradio.FileData" or ("path" in value and ("url" in value or "orig_name" in value)):
            found.append(value)
        else:
            for item in value.values():
                found.extend(collect_file_data(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(collect_file_data(item))
    return found


def strip_file_data(value: Any) -> Any:
    """結果から FileData を外し、文字の値（状態の文・seed など）だけ残す（純粋関数）。長い文字は切る。"""
    if isinstance(value, dict):
        if value in collect_file_data(value):
            return "<file>"
        return {key: strip_file_data(item) for key, item in value.items()}
    if isinstance(value, list):
        items = [strip_file_data(item) for item in value[:RESULT_LIST_LIMIT]]
        if len(value) > RESULT_LIST_LIMIT:
            items.append(f"…（{len(value)} 件のうち先頭 {RESULT_LIST_LIMIT} 件だけ）")
        return items
    if isinstance(value, str) and len(value) > RESULT_TEXT_LIMIT:
        return value[:RESULT_TEXT_LIMIT] + f"…（{len(value)} 文字のうち先頭だけ）"
    return value


def file_download_url(base: str, file_data: dict[str, Any]) -> str:
    url = file_data.get("url")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        # Gradio は届いた Host から URL を作る。念のため base のホストへ向け直す。
        parts = urllib.parse.urlsplit(url)
        base_parts = urllib.parse.urlsplit(base)
        return urllib.parse.urlunsplit((base_parts.scheme, base_parts.netloc, parts.path, parts.query, ""))
    if isinstance(url, str) and url.startswith("/"):
        return f"{base}{url}"
    return f"{base}/gradio_api/file={urllib.parse.quote(str(file_data.get('path')))}"


def download_gradio_files(base: str, result: Any, stem: str, directory: str | None) -> list[str]:
    saved = []
    seen = set()
    for file_data in collect_file_data(result):
        url = file_download_url(base, file_data)
        if url in seen:
            continue
        seen.add(url)
        response = http_request("GET", url, timeout=600.0)
        if response.status != 200:
            raise ToolError(f"結果のファイルを取れません（HTTP {response.status}）: {url}")
        original = str(file_data.get("orig_name") or file_data.get("path") or "")
        suffix = Path(urllib.parse.unquote(original)).suffix or Path(urllib.parse.urlsplit(url).path).suffix or ".bin"
        saved.append(save_bytes(response.body, stem, suffix, directory))
    return saved


def gradio_base_for(key: str) -> str:
    service = require_running(key)
    return base_url(int(service["port"]))


def prepare_gradio_values(base: str, parameters: list[dict[str, Any]], args_values: dict[str, Any],
                          file_values: dict[str, Any]) -> dict[str, Any]:
    values = dict(args_values)
    by_name = {str(parameter.get("parameter_name")): parameter for parameter in parameters}
    for name, raw_path in file_values.items():
        if name not in by_name:
            raise ToolError(f"この API に無いファイルの引数です: {name}。使える引数: {', '.join(by_name)}")
        if raw_path in (None, ""):
            values[name] = None
            continue
        if isinstance(raw_path, list):
            values[name] = [gradio_upload(base, resolve_input_path(str(item))) for item in raw_path]
        else:
            values[name] = gradio_upload(base, resolve_input_path(str(raw_path)))
    return values


def run_gradio(key: str, api_name: str, values: dict[str, Any], files: dict[str, Any], stem: str,
               directory: str | None, timeout: float) -> dict[str, Any]:
    base = gradio_base_for(key)
    info = gradio_info(base)
    endpoint_name = "/" + api_name.lstrip("/")
    endpoint = info["named_endpoints"].get(endpoint_name)
    if endpoint is None:
        raise ToolError(f"{key} に API {endpoint_name} がありません。ある API: {', '.join(info['named_endpoints'])}")
    parameters = endpoint.get("parameters") or []
    prepared = prepare_gradio_values(base, parameters, values, files)
    data = build_gradio_data(parameters, prepared)
    started = time.monotonic()
    result = gradio_call(base, endpoint_name, data, timeout)
    elapsed = time.monotonic() - started
    saved = download_gradio_files(base, result, stem, directory)
    return {"api_name": endpoint_name, "saved": saved, "outputs": strip_file_data(result), "elapsed_sec": round(elapsed, 1)}


def tool_gradio_info(args: dict[str, Any]) -> dict[str, Any]:
    key = str(args.get("key") or "").strip()
    if key not in GRADIO_KEYS:
        raise ToolError(f"key は {', '.join(GRADIO_KEYS)} のどれかです。")
    info = gradio_info(gradio_base_for(key))
    endpoints = {}
    for name, endpoint in info["named_endpoints"].items():
        endpoints[name] = [
            {
                "name": parameter.get("parameter_name"),
                "label": parameter.get("label"),
                "component": parameter.get("component"),
                "file": is_file_parameter(parameter),
                "default": parameter.get("parameter_default") if parameter.get("parameter_has_default") else None,
                "required": not parameter.get("parameter_has_default"),
            }
            for parameter in endpoint.get("parameters") or []
        ]
    return {"key": key, "endpoints": endpoints,
            "note": "localai_gradio_call の args に値の引数、files にファイルの引数（ローカルのパス）を名前で渡す。"}


def tool_gradio_call(args: dict[str, Any]) -> dict[str, Any]:
    key = str(args.get("key") or "").strip()
    if key not in GRADIO_KEYS:
        raise ToolError(f"key は {', '.join(GRADIO_KEYS)} のどれかです。")
    api_name = str(args.get("api_name") or "").strip()
    if not api_name:
        raise ToolError("api_name が要ります（localai_gradio_info で一覧が出ます）。")
    values = args.get("args") or {}
    files = args.get("files") or {}
    if not isinstance(values, dict) or not isinstance(files, dict):
        raise ToolError("args と files は辞書です（引数名 → 値 / ローカルのパス）。")
    return run_gradio(key, api_name, values, files, f"{key}_{api_name.strip('/')}",
                      args.get("output_dir"), float(args.get("timeout_sec") or 1800))


def tool_tts(args: dict[str, Any]) -> dict[str, Any]:
    text = str(args.get("text") or "").strip()
    design = str(args.get("design") or "").strip()
    if not text or not design:
        raise ToolError("text（読み上げる文）と design（声の特徴の説明）の両方が要ります。")
    values: dict[str, Any] = {"text": text, "design": design}
    if args.get("lang"):
        values["lang_disp"] = str(args["lang"])
    return run_gradio("qwen3-tts", "/run_voice_design", values, {}, "tts",
                      args.get("output_dir"), float(args.get("timeout_sec") or 600))


def choose_mesh_endpoint(info: dict[str, Any], engine: str, texture: bool, view_params: list[str]) -> str:
    """3D 生成に使う api_name を選ぶ（純粋関数）。候補の名前を優先し、無ければ画像の引数を全部持つものを探す。"""
    named = info["named_endpoints"]
    for candidate in MESH_ENGINES[engine]["endpoints"][texture]:
        if candidate in named:
            return candidate
    for name, endpoint in named.items():
        names = {parameter.get("parameter_name") for parameter in endpoint.get("parameters") or []}
        if all(view in names for view in view_params):
            return name
    raise ToolError(f"{engine} に 4 方向の画像を受け取る API が見つかりません。ある API: {', '.join(named)}")


def tool_mesh(args: dict[str, Any]) -> dict[str, Any]:
    engine = str(args.get("engine") or "").strip()
    if engine not in MESH_ENGINES:
        raise ToolError("engine は 'hunyuan3d' か 'pixal3d' です。")
    views = MESH_ENGINES[engine]["views"]
    files = {views[view]: args[view] for view in views if args.get(view)}
    if views["front"] not in files:
        raise ToolError("front（正面の画像のパス）が要ります。back・left・right は任意です。")
    texture = bool(args.get("texture", False))
    options = args.get("options") or {}
    if not isinstance(options, dict):
        raise ToolError("options は辞書です（引数名 → 値。localai_gradio_info で一覧が出ます）。")

    base = gradio_base_for(engine)
    info = gradio_info(base)
    endpoint_name = choose_mesh_endpoint(info, engine, texture, list(files))
    parameters = info["named_endpoints"][endpoint_name].get("parameters") or []
    prepared = prepare_gradio_values(base, parameters, options, files)
    data = build_gradio_data(parameters, prepared)
    started = time.monotonic()
    result = gradio_call(base, endpoint_name, data, float(args.get("timeout_sec") or 1800))
    elapsed = time.monotonic() - started
    saved = download_gradio_files(base, result, f"mesh_{engine}", args.get("output_dir"))
    return {"engine": engine, "api_name": endpoint_name, "saved": saved,
            "outputs": strip_file_data(result), "elapsed_sec": round(elapsed, 1)}


# ----------------------------------------------------------------------------------------------------------------
# ComfyUI

def comfy_base() -> str:
    return gradio_base_for("comfyui")


def comfy_upload_image(base: str, path: Path) -> str:
    body, content_type = build_multipart([("overwrite", "true")], [("image", path.name, path.read_bytes())])
    response = http_request("POST", f"{base}/upload/image", body=body, headers={"Content-Type": content_type}, timeout=300.0)
    if response.status != 200:
        raise ToolError(f"ComfyUI への画像のアップロードが HTTP {response.status} で失敗しました: {response.error_text()}")
    result = response.json() or {}
    return f"{result['subfolder']}/{result['name']}" if result.get("subfolder") else result["name"]


def is_ui_workflow(workflow: Any) -> bool:
    return isinstance(workflow, dict) and isinstance(workflow.get("nodes"), list) and "links" in workflow


def apply_overrides(prompt: dict[str, Any], overrides: dict[str, Any]) -> list[str]:
    """{"NODE.INPUT": 値} を API 形式の prompt に書く（純粋関数ではない: prompt を変える）。書いた先を返す。"""
    written = []
    for target, value in overrides.items():
        node_id, dot, input_name = str(target).partition(".")
        if not dot or not node_id or not input_name:
            raise ToolError(f"set のキーは 'ノード番号.入力名' の形です: {target}")
        node = prompt.get(node_id)
        if not isinstance(node, dict):
            raise ToolError(f"ワークフローにノード {node_id} がありません。")
        node.setdefault("inputs", {})[input_name] = value
        written.append(f"{node_id}.{input_name}")
    return written


def comfy_history_files(entry: dict[str, Any]) -> list[dict[str, str]]:
    files = []
    seen = set()
    for node_id, node_output in (entry.get("outputs") or {}).items():
        if not isinstance(node_output, dict):
            continue
        for items in node_output.values():
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and item.get("filename"):
                    ident = (item.get("type") or "output", item.get("subfolder") or "", item["filename"])
                    if ident in seen:
                        continue
                    seen.add(ident)
                    files.append({"node": str(node_id), "type": ident[0], "subfolder": ident[1], "filename": ident[2]})
    return files


def comfy_collect(base: str, prompt_id: str, directory: str | None, include_temp: bool) -> dict[str, Any] | None:
    """/history を 1 回見る。終わっていなければ None。終わっていれば出力を落として結果を返す。"""
    response = http_json("GET", f"{base}/history/{urllib.parse.quote(prompt_id)}", timeout=30.0)
    if response.status != 200:
        raise ToolError(f"ComfyUI の /history が HTTP {response.status} を返しました。")
    entry = (response.json() or {}).get(prompt_id)
    if not entry:
        return None
    status = entry.get("status") or {}
    if not status.get("completed") and status.get("status_str") != "error":
        return None
    saved = []
    for item in comfy_history_files(entry):
        if item["type"] == "temp" and not include_temp:
            continue
        query = urllib.parse.urlencode({"filename": item["filename"], "subfolder": item["subfolder"], "type": item["type"]})
        file_response = http_request("GET", f"{base}/view?{query}", timeout=600.0)
        if file_response.status != 200:
            raise ToolError(f"出力 {item['filename']} を取れません（HTTP {file_response.status}）。")
        stem = Path(item["filename"]).stem
        saved.append(save_bytes(file_response.body, f"comfyui_{stem}", Path(item["filename"]).suffix, directory))
    messages = [message for message in status.get("messages") or [] if isinstance(message, list) and message
                and message[0] in ("execution_error", "execution_interrupted")]
    return {
        "prompt_id": prompt_id,
        "status": status.get("status_str") or ("success" if status.get("completed") else "unknown"),
        "saved": saved,
        "errors": strip_file_data(messages),
    }


def comfy_wait(base: str, prompt_id: str, timeout: float, directory: str | None, include_temp: bool) -> dict[str, Any]:
    started = time.monotonic()
    while True:
        result = comfy_collect(base, prompt_id, directory, include_temp)
        elapsed = time.monotonic() - started
        if result is not None:
            result["waited_sec"] = round(elapsed, 1)
            return result
        if elapsed >= timeout:
            queue = http_json("GET", f"{base}/queue", timeout=30.0)
            position = None
            if queue.status == 200:
                body = queue.json() or {}
                running = [item[1] for item in body.get("queue_running") or [] if len(item) > 1]
                pending = [item[1] for item in body.get("queue_pending") or [] if len(item) > 1]
                position = "running" if prompt_id in running else (pending.index(prompt_id) + 1 if prompt_id in pending else None)
            return {"prompt_id": prompt_id, "status": "pending", "queue_position": position, "waited_sec": round(elapsed, 1),
                    "note": "まだ終わっていません。localai_comfyui_result で prompt_id を渡して続きを待ってください。"}
        time.sleep(COMFY_POLL_SEC)


def comfy_saved_workflow_url(base: str, name: str) -> str:
    """サーバーの user/default/workflows/<name> を読む URL（純粋関数）。`..` や絶対パスは断る。"""
    parts = [part for part in name.replace("\\", "/").split("/") if part]
    if not parts or name.startswith("/") or any(part in (".", "..") for part in parts) or not parts[-1].endswith(".json"):
        raise ToolError("workflow_name は workflows/ からの相対パスで、.json で終わる名前です（例 VFX/WanAlpha_T2V_api.json）。")
    return f"{base}/api/userdata/{urllib.parse.quote('workflows/' + '/'.join(parts), safe='')}"


def tool_comfyui(args: dict[str, Any]) -> dict[str, Any]:
    raw_workflow = args.get("workflow")
    if args.get("workflow_name"):
        response = http_json("GET", comfy_saved_workflow_url(comfy_base(), str(args["workflow_name"])), timeout=30.0)
        if response.status != 200:
            raise ToolError(f"サーバーにワークフロー {args['workflow_name']} がありません（HTTP {response.status}）。")
        raw_workflow = response.json()
    if args.get("workflow_path"):
        try:
            raw_workflow = json.loads(resolve_input_path(str(args["workflow_path"])).read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ToolError(f"ワークフローが JSON として読めません: {error}") from error
    if not isinstance(raw_workflow, dict) or not raw_workflow:
        raise ToolError("workflow_name（サーバーに保存したワークフロー）か workflow_path（API 形式の JSON のパス）か workflow（その中身）が要ります。")
    if is_ui_workflow(raw_workflow):
        raise ToolError(
            "これは画面で保存する UI 形式のワークフローです。API 形式が要ります。ComfyUI の画面の「Export (API)」で保存するか、"
            "サーバーで tools/comfyui/scripts/run_workflow.py convert WORKFLOW.json -o api.json を実行してください。"
        )
    prompt = json.loads(json.dumps(raw_workflow))
    base = comfy_base()

    uploaded: dict[str, str] = {}
    image_inputs = args.get("image_inputs") or {}
    if not isinstance(image_inputs, dict):
        raise ToolError("image_inputs は {\"ノード番号.入力名\": ローカルの画像のパス} の辞書です。")
    overrides = dict(args.get("set") or {})
    for target, raw_path in image_inputs.items():
        name = comfy_upload_image(base, resolve_input_path(str(raw_path)))
        uploaded[str(raw_path)] = name
        overrides[target] = name
    for raw_path in args.get("upload_images") or []:
        uploaded[str(raw_path)] = comfy_upload_image(base, resolve_input_path(str(raw_path)))
    written = apply_overrides(prompt, overrides)

    response = http_json("POST", f"{base}/prompt", payload={"prompt": prompt, "client_id": uuid.uuid4().hex}, timeout=120.0)
    body = None
    try:
        body = response.json()
    except ToolError:
        pass
    if response.status != 200 or not isinstance(body, dict) or not body.get("prompt_id"):
        detail = json.dumps(body, ensure_ascii=False)[:3000] if body is not None else response.error_text()
        raise ToolError(f"ComfyUI が投入を断りました（HTTP {response.status}）: {detail}")
    prompt_id = body["prompt_id"]
    output: dict[str, Any] = {"prompt_id": prompt_id, "uploaded": uploaded, "set": written}
    if not args.get("wait", True):
        output["status"] = "queued"
        output["note"] = "localai_comfyui_result で prompt_id を渡すと、終わりを待って出力を受け取れます。"
        return output
    output.update(comfy_wait(base, prompt_id, float(args.get("timeout_sec") or 1800),
                             args.get("output_dir"), bool(args.get("include_temp", False))))
    return output


# localai_vfx: ゲームのエフェクト（Wan 2.1 Alpha / LayerDiffuse）を名前付きの引数で作る。入力の対応（どのノードのどの入力か）は
# 入口ページの GET /api の comfyui.workflows から読む（サーバー側の tools/portal/portal.py の COMFYUI_API_WORKFLOWS が正本）。
VFX_KINDS = {
    "wan_alpha": {"workflow": "VFX/WanAlpha_T2V_api.json", "size_multiple": 16, "frames": True},
    "layerdiffuse": {"workflow": "VFX/LayerDiffuse_SDXL_RGBA_api.json", "size_multiple": 64, "frames": False},
}
VFX_VALUE_ARGS = ("prompt", "negative_prompt", "seed", "steps", "cfg", "width", "height", "frames")


def vfx_overrides(kind: str, inputs_map: dict[str, str], args: dict[str, Any]) -> dict[str, Any]:
    """名前付きの引数を {"ノード.入力": 値} に直す（純粋関数）。大きさ・コマ数の決まりもここで確かめる。"""
    spec = VFX_KINDS[kind]
    overrides: dict[str, Any] = {}
    for name in VFX_VALUE_ARGS:
        value = args.get(name)
        if value is None:
            continue
        if name == "frames" and not spec["frames"]:
            raise ToolError("frames は wan_alpha だけで使えます（layerdiffuse は 1 枚絵）。")
        if name in ("width", "height") and (int(value) <= 0 or int(value) % spec["size_multiple"]):
            raise ToolError(f"{kind} の {name} は {spec['size_multiple']} の倍数にしてください（{value}）。")
        if name == "frames" and (int(value) < 1 or (int(value) - 1) % 4):
            raise ToolError(f"frames は 4 の倍数 + 1 にしてください（例 49・81。指定は {value}）。")
        target = inputs_map.get(name)
        if not target:
            raise ToolError(f"サーバーのワークフローに {name} の入力の対応がありません（GET /api の comfyui.workflows）。")
        overrides[target] = int(value) if name in ("seed", "steps", "width", "height", "frames") else (
            float(value) if name == "cfg" else str(value))
    prefix = args.get("filename_prefix")
    if prefix:
        if "/" in str(prefix).strip("/") or ".." in str(prefix):
            raise ToolError("filename_prefix は名前だけにしてください（dq_vfx_trial/ の下に置きます）。")
        for key in ("png_filename_prefix", "filename_prefix"):
            if key in inputs_map:
                overrides[inputs_map[key]] = f"dq_vfx_trial/{prefix}"
        if "webp_filename_prefix" in inputs_map:
            overrides[inputs_map["webp_filename_prefix"]] = f"dq_vfx_trial/{prefix}_webp"
    return overrides


def tool_vfx(args: dict[str, Any]) -> dict[str, Any]:
    kind = str(args.get("kind") or "").strip()
    if kind not in VFX_KINDS:
        raise ToolError(f"kind は {' か '.join(VFX_KINDS)} です。")
    if not str(args.get("prompt") or "").strip():
        raise ToolError("prompt が要ります（英語。透明な背景の素材なら、背景が無いことも書く）。")
    workflow_name = VFX_KINDS[kind]["workflow"]
    response = http_json("GET", f"{portal_url()}/api", timeout=30.0)
    catalog = response.json() if response.status == 200 else {}
    entries = {item.get("name"): item for item in ((catalog.get("comfyui") or {}).get("workflows") or []) if isinstance(item, dict)}
    if workflow_name not in entries:
        raise ToolError(f"入口ページの GET /api に {workflow_name} がありません。サーバーの入口ページが古い可能性があります。")
    overrides = vfx_overrides(kind, entries[workflow_name].get("inputs") or {}, args)

    if args.get("start"):
        started = tool_service({"key": "comfyui", "action": "start", "stop_others": bool(args.get("stop_others", False)),
                                "timeout_sec": 900})
        if not started.get("ok"):
            return {"kind": kind, "status": "not_started", "start": started}
    result = tool_comfyui({
        "workflow_name": workflow_name, "set": overrides, "wait": args.get("wait", True),
        "timeout_sec": args.get("timeout_sec") or 1800, "output_dir": args.get("output_dir"),
    })
    return {"kind": kind, "workflow_name": workflow_name, **result}


def tool_comfyui_result(args: dict[str, Any]) -> dict[str, Any]:
    prompt_id = str(args.get("prompt_id") or "").strip()
    if not prompt_id:
        raise ToolError("prompt_id が要ります。")
    base = comfy_base()
    # wait=false なら 1 回だけ見る（timeout 0 で comfy_wait は 1 回見て返る）。
    timeout = float(args.get("timeout_sec") or 1800) if args.get("wait", True) else 0.0
    return comfy_wait(base, prompt_id, timeout, args.get("output_dir"), bool(args.get("include_temp", False)))


# ----------------------------------------------------------------------------------------------------------------
# ツールの定義

def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


OUTPUT_DIR_PROP = {"type": "string", "description": "出力の保存先（省略時は LOCALAI_OUTPUT_DIR か ./localai-outputs）"}
TIMEOUT_PROP = {"type": "number", "description": "待つ上限の秒数"}
AUDIO_PATH_PROP = {"type": "string", "description": "元の音（続き・メロディ・音→音に使う、この PC のファイルのパス）"}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "localai_status",
        "description": "A30 サーバーの入口ページ（8080）の全サービスの状態（running / starting / stopped）、GPU の使用量、Ollama に載っているモデルを返す。",
        "inputSchema": _schema({}),
        "handler": tool_status,
    },
    {
        "name": "localai_service",
        "description": (
            "サービスを起動・停止する（入口ページの API）。key は localai_status の key（open-webui, strata, strata-coder, strata-official, "
            "strata-official-iq3s, strata-swift, hunyuan3d, pixal3d, comfyui, audiocraft, qwen3-tts, stable-audio）。GPU を使うものは同時に 1 つだけで、"
            "起動すると他が止まる。止まるもの・Ollama から降ろされるモデルがあるときは stop_others=true が無いと起動せず、その一覧を返す。"
            "起動は Strata・Hunyuan3D で数分かかる。wait=true なら使えるようになるまで待つ（入口ページの ?wait=。起動に失敗したら ok=false で返る）。"
        ),
        "inputSchema": _schema({
            "key": {"type": "string"},
            "action": {"type": "string", "enum": ["start", "stop"]},
            "wait": {"type": "boolean", "description": "使える（止まる）まで待つ。既定 true"},
            "stop_others": {"type": "boolean", "description": "稼働中の他の GPU サービスを止め、Ollama のモデルを降ろしてよい。既定 false"},
            "timeout_sec": {"type": "number", "description": "待つ上限。既定 900"},
        }, ["key", "action"]),
        "handler": tool_service,
    },
    {
        "name": "localai_ollama",
        "description": (
            "Ollama のモデル（qwen3.8:27b など）を入口ページ経由で操作する。action=list で入っているモデルと VRAM に載っているモデル、"
            "load で VRAM に載せる（載せ終わるまで待つ。初回は約 60 秒）、unload で降ろす。Qwen 27B は VRAM を 22 GiB 使うので、"
            "load は GPU を使うサービス（Strata など）が動いていると stop_others=true が無い限り読み込まず、止まるものの一覧を返す。"
            "Ollama そのものは LAN に出ていないので、載せたモデルとの会話は Open WebUI で行う。"
        ),
        "inputSchema": _schema({
            "action": {"type": "string", "enum": ["list", "load", "unload"], "description": "既定 list"},
            "model": {"type": "string", "description": "モデル名（例 qwen3.8:27b）。load / unload で必須"},
            "keep_alive": {"type": "string", "description": "load で載せておく長さ。秒の整数・30m・2h・-1（無期限）。省略時は Ollama の既定 5 分"},
            "stop_others": {"type": "boolean", "description": "稼働中の GPU サービスを止めてよい。既定 false"},
            "timeout_sec": {"type": "number", "description": "HTTP の待ちの上限。既定 load 600・unload 60"},
        }),
        "handler": tool_ollama,
    },
    {
        "name": "localai_chat",
        "description": (
            "Strata（Qwen3.8 Flash Next の MoE、OpenAI 互換）に質問する。model は strata（汎用 RVN IQ3_S）、strata-coder（コード向け）、"
            "strata-official（公式 IQ3_XXS）、strata-official-iq3s（公式 IQ3_S）、strata-swift（思考が短い Swift 1.5）。先に localai_service で起動しておく。"
            "思考（reasoning）は既定で文字数だけ返す。"
        ),
        "inputSchema": _schema({
            "model": {"type": "string", "enum": list(STRATA_KEYS)},
            "prompt": {"type": "string"},
            "messages": {"type": "array", "items": {"type": "object"}, "description": "prompt の代わりに OpenAI 形式の会話"},
            "system": {"type": "string"},
            "max_tokens": {"type": "integer"},
            "temperature": {"type": "number"},
            "top_p": {"type": "number"},
            "extra": {"type": "object", "description": "本文にそのまま足す項目"},
            "include_reasoning": {"type": "boolean"},
            "save_to_file": {"type": "boolean", "description": "要求と応答の全文を JSON で保存する"},
            "output_dir": OUTPUT_DIR_PROP,
            "timeout_sec": {"type": "number", "description": "既定 1800"},
        }),
        "handler": tool_chat,
    },
    {
        "name": "localai_music",
        "description": (
            "AudioCraft の MusicGen Medium で作曲し、wav（32 kHz）をこの PC に保存する。prompt は英語。duration は 1〜30 秒。"
            "mode は text（既定）・continuation（audio_path の続き）・melody（audio_path のメロディ）。先に audiocraft を起動しておく。"
        ),
        "inputSchema": _schema({
            "prompt": {"type": "string"}, "duration": {"type": "number"}, "seed": {"type": "integer"},
            "top_k": {"type": "integer"}, "top_p": {"type": "number"}, "temperature": {"type": "number"},
            "cfg_coef": {"type": "number"}, "mode": {"type": "string", "enum": ["text", "continuation", "melody"]},
            "audio_path": AUDIO_PATH_PROP, "keep": {"type": "boolean", "description": "サーバーにも残す（既定 true）"},
            "output_dir": OUTPUT_DIR_PROP, "timeout_sec": TIMEOUT_PROP,
        }, ["prompt"]),
        "handler": tool_music,
    },
    {
        "name": "localai_sound",
        "description": "AudioCraft の AudioGen Medium で効果音を作り、wav（16 kHz）をこの PC に保存する。prompt は英語。duration は 1〜20 秒。mode は text か continuation。",
        "inputSchema": _schema({
            "prompt": {"type": "string"}, "duration": {"type": "number"}, "seed": {"type": "integer"},
            "top_k": {"type": "integer"}, "top_p": {"type": "number"}, "temperature": {"type": "number"},
            "cfg_coef": {"type": "number"}, "mode": {"type": "string", "enum": ["text", "continuation"]},
            "audio_path": AUDIO_PATH_PROP, "keep": {"type": "boolean"},
            "output_dir": OUTPUT_DIR_PROP, "timeout_sec": TIMEOUT_PROP,
        }, ["prompt"]),
        "handler": tool_sound,
    },
    {
        "name": "localai_stable_audio",
        "description": (
            "Stable Audio 3.0 Medium で曲・効果音を作り、wav（44.1 kHz ステレオ）をこの PC に保存する。prompt は英語。duration は 1〜380 秒。"
            "mode は text（既定）・audio（audio_path を元に作り直す。init_noise_level 0〜1）・inpaint（inpaint_start〜inpaint_end 秒を作り直す・続き）。"
        ),
        "inputSchema": _schema({
            "prompt": {"type": "string"}, "duration": {"type": "number"}, "seed": {"type": "integer"},
            "steps": {"type": "integer"}, "mode": {"type": "string", "enum": ["text", "audio", "inpaint"]},
            "audio_path": AUDIO_PATH_PROP, "init_noise_level": {"type": "number"},
            "inpaint_start": {"type": "number"}, "inpaint_end": {"type": "number"}, "keep": {"type": "boolean"},
            "output_dir": OUTPUT_DIR_PROP, "timeout_sec": TIMEOUT_PROP,
        }, ["prompt"]),
        "handler": tool_stable_audio,
    },
    {
        "name": "localai_tts",
        "description": "Qwen3-TTS VoiceDesign で、design（声の特徴の文章。日本語可）の声で text を読み上げ、wav をこの PC に保存する。lang は Auto（既定）・Japanese・English など。",
        "inputSchema": _schema({
            "text": {"type": "string"}, "design": {"type": "string"}, "lang": {"type": "string"},
            "output_dir": OUTPUT_DIR_PROP, "timeout_sec": TIMEOUT_PROP,
        }, ["text", "design"]),
        "handler": tool_tts,
    },
    {
        "name": "localai_mesh",
        "description": (
            "正面（必須）・背面・左・右の画像（この PC のファイル）から 3D メッシュを作り、glb などをこの PC に保存する。engine は hunyuan3d か pixal3d。"
            "hunyuan3d は texture=true でテクスチャ付き（generation_all）。細かい設定は options に引数名で渡す（localai_gradio_info で一覧）。数分かかる。"
        ),
        "inputSchema": _schema({
            "engine": {"type": "string", "enum": list(MESH_ENGINES)},
            "front": {"type": "string"}, "back": {"type": "string"}, "left": {"type": "string"}, "right": {"type": "string"},
            "texture": {"type": "boolean"}, "options": {"type": "object"},
            "output_dir": OUTPUT_DIR_PROP, "timeout_sec": {"type": "number", "description": "既定 1800"},
        }, ["engine", "front"]),
        "handler": tool_mesh,
    },
    {
        "name": "localai_comfyui",
        "description": (
            "ComfyUI に API 形式のワークフロー（画面の Export (API) の JSON）を投入し、終わったら出力（画像・動画・メッシュ）をこの PC に保存する。"
            "image_inputs={\"ノード番号.入力名\": 画像のパス} で画像を上げてその入力に入れる。set={\"ノード番号.入力名\": 値} で値を書き換える。"
            "動画など長いものは wait=false で投げ、localai_comfyui_result で受け取る。"
            "workflow_name でサーバーに保存した API 形式を名前で使える（例 VFX/WanAlpha_T2V_api.json・VFX/LayerDiffuse_SDXL_RGBA_api.json。"
            "入力の対応は入口ページの GET /api の comfyui.workflows）。"
        ),
        "inputSchema": _schema({
            "workflow_name": {"type": "string", "description": "サーバーの workflows/ からの相対パス（API 形式）"},
            "workflow_path": {"type": "string"}, "workflow": {"type": "object"},
            "image_inputs": {"type": "object"}, "upload_images": {"type": "array", "items": {"type": "string"}},
            "set": {"type": "object"}, "wait": {"type": "boolean"}, "include_temp": {"type": "boolean"},
            "output_dir": OUTPUT_DIR_PROP, "timeout_sec": {"type": "number", "description": "既定 1800"},
        }),
        "handler": tool_comfyui,
    },
    {
        "name": "localai_vfx",
        "description": (
            "ゲームのエフェクトを作る。kind=wan_alpha は透明度つきの動画（Wan 2.1 Alpha。RGBA の PNG の連番とアニメ WebP、"
            "832x480・81 コマで約 2 分）、kind=layerdiffuse は透明な 1 枚絵（SDXL + LayerDiffuse。RGBA の PNG、1024x1024 で約 10 秒）。"
            "出力はこの PC に保存する。ComfyUI が止まっていれば start=true で起動する（GPU を使うほかのサービスが動いていれば、"
            "stop_others=true が無い限り起動しない）。wan_alpha は長いので wait=false で投げて localai_comfyui_result で受け取ってもよい。"
        ),
        "inputSchema": _schema({
            "kind": {"type": "string", "enum": list(VFX_KINDS)},
            "prompt": {"type": "string", "description": "英語。例 a swirling cloud of glowing violet magic mist, centered, no background"},
            "negative_prompt": {"type": "string"},
            "seed": {"type": "integer", "description": "既定 42（色や形はシードで大きく変わる）"},
            "steps": {"type": "integer", "description": "既定 wan_alpha 4（LightX2V）・layerdiffuse 20"},
            "cfg": {"type": "number", "description": "既定 wan_alpha 1.0・layerdiffuse 7"},
            "width": {"type": "integer", "description": "wan_alpha は 16 の倍数（既定 832）、layerdiffuse は 64 の倍数（既定 1024）"},
            "height": {"type": "integer", "description": "wan_alpha 既定 480、layerdiffuse 既定 1024"},
            "frames": {"type": "integer", "description": "wan_alpha だけ。4 の倍数 + 1（既定 81 = 16 fps で約 5 秒）"},
            "filename_prefix": {"type": "string", "description": "サーバーの output/dq_vfx_trial/ の下のファイル名の頭"},
            "start": {"type": "boolean", "description": "ComfyUI が止まっていれば起動する（既定 false）"},
            "stop_others": {"type": "boolean", "description": "start のとき、GPU を使うほかのサービスを止めてよいか（既定 false）"},
            "wait": {"type": "boolean"}, "output_dir": OUTPUT_DIR_PROP,
            "timeout_sec": {"type": "number", "description": "既定 1800"},
        }, ["kind", "prompt"]),
        "handler": tool_vfx,
    },
    {
        "name": "localai_comfyui_result",
        "description": "localai_comfyui で投げた prompt_id の終わりを待ち（wait=false なら 1 回だけ見る）、出力をこの PC に保存する。",
        "inputSchema": _schema({
            "prompt_id": {"type": "string"}, "wait": {"type": "boolean"}, "include_temp": {"type": "boolean"},
            "output_dir": OUTPUT_DIR_PROP, "timeout_sec": {"type": "number", "description": "既定 1800"},
        }, ["prompt_id"]),
        "handler": tool_comfyui_result,
    },
    {
        "name": "localai_gradio_info",
        "description": "Gradio で動くサービス（hunyuan3d, pixal3d, audiocraft, qwen3-tts, stable-audio）の API の一覧と、引数の名前・既定値・ファイルかどうかを返す。",
        "inputSchema": _schema({"key": {"type": "string", "enum": list(GRADIO_KEYS)}}, ["key"]),
        "handler": tool_gradio_info,
    },
    {
        "name": "localai_gradio_call",
        "description": (
            "Gradio の API を名前で呼ぶ汎用ツール（専用ツールに無い機能用）。args に値の引数、files にファイルの引数（この PC のパス）を名前で渡す。"
            "省いた引数は既定値になる。結果のファイルはこの PC に保存する。"
        ),
        "inputSchema": _schema({
            "key": {"type": "string", "enum": list(GRADIO_KEYS)}, "api_name": {"type": "string"},
            "args": {"type": "object"}, "files": {"type": "object"},
            "output_dir": OUTPUT_DIR_PROP, "timeout_sec": {"type": "number", "description": "既定 1800"},
        }, ["key", "api_name"]),
        "handler": tool_gradio_call,
    },
]
TOOLS_BY_NAME: dict[str, dict[str, Any]] = {tool["name"]: tool for tool in TOOLS}


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """tools/call の result を作る。失敗は isError=true の結果にする（JSON-RPC のエラーにしない）。"""
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        return {"content": [{"type": "text", "text": f"知らないツールです: {name}"}], "isError": True}
    handler: Callable[[dict[str, Any]], dict[str, Any]] = tool["handler"]
    try:
        result = handler(arguments or {})
    except ToolError as error:
        return {"content": [{"type": "text", "text": str(error)}], "isError": True}
    except Exception as error:  # noqa: BLE001 — 想定外の失敗も MCP の結果として返し、サーバーは落とさない
        traceback.print_exc(file=sys.stderr)
        return {"content": [{"type": "text", "text": f"想定外の失敗: {type(error).__name__}: {error}"}], "isError": True}
    return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}], "isError": False}


# ----------------------------------------------------------------------------------------------------------------
# MCP（JSON-RPC 2.0、改行区切りの stdio）

class McpServer:
    def __init__(self, reader: Any, writer: Any) -> None:
        self.reader = reader
        self.writer = writer
        self.write_lock = threading.Lock()
        self.workers: list[threading.Thread] = []

    def send(self, message: dict[str, Any]) -> None:
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        with self.write_lock:
            self.writer.write(data)
            self.writer.flush()

    def respond(self, request_id: Any, result: dict[str, Any]) -> None:
        self.send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def respond_error(self, request_id: Any, code: int, message: str) -> None:
        self.send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})

    def handle(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if request_id is None:
            return  # 通知（notifications/initialized・cancelled など）には応えない
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
            self.respond(request_id, {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "A30 サーバーの AI（Strata のチャット・音楽/効果音・読み上げ・3D 生成・ComfyUI）を使う。まず localai_status で状態を見て、"
                    "使うサービスを localai_service で起動してから各ツールを呼ぶ。GPU は 1 枚なので、起動すると他の GPU サービスが止まる。"
                    "他人の作業を止めることになる場合は stop_others=true の前に利用者に確かめる。出力はこの PC のファイルに保存され、パスが返る。"
                ),
            })
        elif method == "ping":
            self.respond(request_id, {})
        elif method == "tools/list":
            self.respond(request_id, {"tools": [
                {key: tool[key] for key in ("name", "description", "inputSchema")} for tool in TOOLS
            ]})
        elif method == "tools/call":
            name = str(params.get("name") or "")
            arguments = params.get("arguments") or {}
            # 生成は数分かかるので別スレッドで行い、その間も ping などに応える。
            worker = threading.Thread(
                target=lambda: self.respond(request_id, call_tool(name, arguments)), daemon=True
            )
            worker.start()
            self.workers.append(worker)
        elif method in ("resources/list", "prompts/list"):
            self.respond(request_id, {"resources": []} if method == "resources/list" else {"prompts": []})
        else:
            self.respond_error(request_id, -32601, f"Method not found: {method}")

    def serve(self) -> None:
        for raw in self.reader:
            line = raw.strip()
            if not line:
                continue
            try:
                message = json.loads(line.decode("utf-8") if isinstance(line, bytes) else line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.respond_error(None, -32700, "Parse error")
                continue
            if not isinstance(message, dict):
                self.respond_error(None, -32600, "Invalid Request")
                continue
            self.handle(message)
        for worker in self.workers:
            worker.join()


# ----------------------------------------------------------------------------------------------------------------
# 自己点検（導入スクリプトが呼ぶ）

def port_is_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def run_check() -> int:
    try:
        host = server_host()
        status = fetch_status()
    except ToolError as error:
        print(f"NG: {error}")
        return 1
    print(f"OK: 入口ページ {portal_url()} に届きました。")
    problems = 0
    for service in status["services"]:
        port = service.get("port")
        state = service.get("state")
        if state == "running":
            reachable = port_is_open(host, int(port))
            mark = "OK" if reachable else "NG"
            note = "" if reachable else "  ← サーバーでは動いているのに届かない。サーバーの ufw でこのポートを LAN に開ける"
            problems += 0 if reachable else 1
            print(f"  {mark} {service.get('key'):<22} {port:<5} 稼働中{note}")
        else:
            print(f"  -- {service.get('key'):<22} {port:<5} {state}（止まっているので届くかは確かめられない）")
    return 1 if problems else 0


def main(argv: list[str]) -> int:
    if "--check" in argv:
        return run_check()
    if "--version" in argv:
        print(f"{SERVER_NAME} {SERVER_VERSION}")
        return 0
    McpServer(sys.stdin.buffer, sys.stdout.buffer).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
