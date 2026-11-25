"""localai_mcp.py の試験。実機には触れず、標準ライブラリの偽 HTTP サーバー 1 つで入口ページと各サービスの口を真似る。

    uvx --with pytest pytest tools/localai-mcp/tests -q
"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

TOOL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOL_DIR.parents[1]
sys.path.insert(0, str(TOOL_DIR))

import localai_mcp as mcp  # noqa: E402

WAV_BYTES = b"RIFF\x24\x00\x00\x00WAVEfmt fake"
GLB_BYTES = b"glTF fake mesh"
PNG_BYTES = b"\x89PNG fake image"


def load_portal():
    spec = importlib.util.spec_from_file_location("portal_for_test", REPO_ROOT / "tools/portal/portal.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------------------------------------
# 偽のサーバー

class FakeState:
    def __init__(self) -> None:
        self.states = {key: "stopped" for key in (
            "open-webui", "strata", "strata-coder", "strata-official", "strata-official-iq3s", "strata-swift",
            "hunyuan3d", "pixal3d", "comfyui", "audiocraft", "qwen3-tts", "stable-audio")}
        self.ollama_models: list[str] = []
        self.port = 0
        self.requests: list[tuple[str, str, dict[str, str], bytes]] = []
        self.uploads: list[str] = []
        self.gradio_calls: dict[str, list[Any]] = {}
        self.comfy_prompts: dict[str, Any] = {}
        self.comfy_done = True
        self.gradio_fail = False
        # 入口ページの ?wait= と /api/ollama（2026-11-25 追加）の振る舞い
        self.old_portal = False          # True なら ?wait= を知らず、/api/ollama も無い古い入口ページ
        self.start_fails = False         # True なら ?wait= 付きの起動で、入口ページが起動失敗と判断する
        self.ollama_installed = ["qwen3.8:27b", "gemma4:e4b"]


GRADIO_INFO = {
    "named_endpoints": {
        "/run_voice_design": {"parameters": [
            {"parameter_name": "text", "parameter_has_default": False, "component": "Textbox"},
            {"parameter_name": "lang_disp", "parameter_has_default": True, "parameter_default": "Auto", "component": "Dropdown"},
            {"parameter_name": "design", "parameter_has_default": False, "component": "Textbox"},
        ]},
        "/_generate": {"parameters": [
            {"parameter_name": "front_image", "parameter_has_default": False, "component": "Image"},
            {"parameter_name": "right_image", "parameter_has_default": True, "parameter_default": None, "component": "Image"},
            {"parameter_name": "back_image", "parameter_has_default": True, "parameter_default": None, "component": "Image"},
            {"parameter_name": "left_image", "parameter_has_default": True, "parameter_default": None, "component": "Image"},
            {"parameter_name": "seed", "parameter_has_default": True, "parameter_default": 0, "component": "Number"},
        ]},
    },
    "unnamed_endpoints": {},
}


def make_handler(state: FakeState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # 静かにする
            pass

        def _body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length) if length else b""

        def _send(self, code: int, body: bytes, content_type: str = "application/json", headers: dict[str, str] | None = None):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, payload: Any, headers: dict[str, str] | None = None):
            self._send(code, json.dumps(payload).encode(), headers=headers)

        def do_GET(self) -> None:
            state.requests.append(("GET", self.path, dict(self.headers.items()), b""))
            path = self.path
            if path == "/api/status":
                return self._json(200, {
                    "gpu": {"used_mib": 1, "total_mib": 24576, "available": True},
                    "ollama": {"reachable": True, "models": [{"name": name} for name in state.ollama_models]},
                    "services": [{"key": key, "name": key.upper(), "port": state.port, "state": value}
                                 for key, value in state.states.items()],
                })
            if path == "/api/ollama" and not state.old_portal:
                return self._json(200, {"reachable": True, "installed": state.ollama_installed,
                                        "loaded": [{"name": name, "size_vram_mib": 22201} for name in state.ollama_models]})
            if path == "/v1/models":
                return self._json(200, {"data": [{"id": "strata-swift:IQ3_XXS"}]})
            if path == "/gradio_api/info":
                return self._json(200, GRADIO_INFO)
            if path.startswith("/gradio_api/call/"):
                name = path.split("/")[3]
                if state.gradio_fail:
                    body = b"event: error\ndata: \"boom\"\n\n"
                elif name == "run_voice_design":
                    result = [{"path": "/tmp/gradio/out.wav", "url": f"http://other-host:{state.port}/gradio_api/file=/tmp/gradio/out.wav",
                               "orig_name": "out.wav", "meta": {"_type": "gradio.FileData"}}, "生成しました"]
                    body = ("event: heartbeat\ndata: null\n\nevent: complete\ndata: " + json.dumps(result) + "\n\n").encode()
                else:
                    result = [{"path": "/tmp/gradio/mesh.glb", "orig_name": "mesh.glb", "meta": {"_type": "gradio.FileData"}},
                              "x" * 5000, list(range(30))]
                    body = ("event: complete\ndata: " + json.dumps(result) + "\n\n").encode()
                return self._send(200, body, "text/event-stream")
            if path.startswith("/gradio_api/file="):
                return self._send(200, GLB_BYTES if path.endswith(".glb") else WAV_BYTES, "application/octet-stream")
            if path.startswith("/history/"):
                prompt_id = path.split("/")[2]
                if prompt_id not in state.comfy_prompts or not state.comfy_done:
                    return self._json(200, {})
                return self._json(200, {prompt_id: {
                    "status": {"status_str": "success", "completed": True, "messages": []},
                    "outputs": {"9": {"images": [
                        {"filename": "out_00001_.png", "subfolder": "", "type": "output"},
                        {"filename": "preview.png", "subfolder": "", "type": "temp"},
                    ]}},
                }})
            if path.startswith("/view?"):
                return self._send(200, PNG_BYTES, "image/png")
            if path.startswith("/api/userdata/"):
                name = urllib.parse.unquote(path[len("/api/userdata/"):])
                if name == "workflows/VFX/Test_api.json":
                    return self._json(200, {"6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a"}}})
                return self._json(404, {"error": "not found"})
            if path == "/queue":
                return self._json(200, {"queue_running": [[0, "p1"]], "queue_pending": []})
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            body = self._body()
            state.requests.append(("POST", self.path, dict(self.headers.items()), body))
            path = self.path
            path, _, query = path.partition("?")
            query_values = dict(urllib.parse.parse_qsl(query))
            if path.startswith("/api/ollama/") and not state.old_portal:
                if self.headers.get("X-Portal-Request") != "1":
                    return self._json(403, {"ok": False, "message": "header"})
                model_part, _, action = path[len("/api/ollama/"):].rpartition("/")
                model = urllib.parse.unquote(model_part)
                if model not in state.ollama_installed:
                    return self._json(404, {"ok": False, "message": f"Ollama に {model} は入っていません。",
                                            "installed": state.ollama_installed})
                if action == "load":
                    stopped = [key for key in mcp.GPU_EXCLUSIVE_KEYS if state.states.get(key) in ("running", "starting")]
                    for key in stopped:
                        state.states[key] = "stopped"
                    state.ollama_models = [model]
                    return self._json(200, {"ok": True, "message": "loaded", "model": model, "stopped": stopped,
                                            "size_vram_mib": 22201, "keep_alive_seen": query_values.get("keep_alive")})
                state.ollama_models = [name for name in state.ollama_models if name != model]
                return self._json(200, {"ok": True, "message": "unloaded", "model": model})
            if path.startswith("/api/service/"):
                if self.headers.get("X-Portal-Request") != "1":
                    return self._json(403, {"ok": False, "message": "header"})
                if query and state.old_portal:
                    return self._json(400, {"ok": False, "message": "action は start か stop のみです。"})
                _, _, _, key, action = path.split("/")
                if action == "start" and "wait" in query_values and state.start_fails:
                    state.states[key] = "stopped"
                    return self._json(200, {"ok": False, "ready": False, "state": "stopped", "waited_sec": 10.0,
                                            "message": "起動に失敗したようです。journalctl を確認してください。", "stopped": []})
                stopped = []
                if action == "start":
                    for other in mcp.GPU_EXCLUSIVE_KEYS:
                        if other != key and state.states.get(other) == "running":
                            state.states[other] = "stopped"
                            stopped.append(other)
                    state.states[key] = "running"
                else:
                    state.states[key] = "stopped"
                payload = {"ok": True, "message": f"{action} ok", "stopped": stopped}
                if action == "start" and "wait" in query_values:
                    payload.update({"ready": True, "state": "running", "waited_sec": 1.5})
                return self._json(200, payload)
            if path == "/v1/chat/completions":
                request = json.loads(body)
                return self._json(200, {"model": request["model"], "choices": [{
                    "message": {"content": "答え: " + request["messages"][-1]["content"], "reasoning_content": "考えた"},
                    "finish_reason": "stop"}], "usage": {"completion_tokens": 3}})
            if path in ("/api/v1/music", "/api/v1/sound", "/api/v1/generate"):
                if self.headers.get("Content-Type") != "application/json":
                    return self._json(415, {"error": "json only"})
                request = json.loads(body)
                if request.get("duration", 1) > 30:
                    return self._json(400, {"error": "長さが範囲外です"})
                prefix = "X-Saudio-" if path.endswith("generate") else "X-Acraft-"
                return self._send(200, WAV_BYTES, "audio/wav", {f"{prefix}Seed": "42", f"{prefix}Elapsed-Sec": "1.0"})
            if path == "/gradio_api/upload":
                name = f"/tmp/gradio/upload{len(state.uploads)}.png"
                state.uploads.append(name)
                return self._json(200, [name])
            if path.startswith("/gradio_api/call/"):
                name = path.split("/")[3]
                state.gradio_calls[name] = json.loads(body)["data"]
                return self._json(200, {"event_id": "ev1"})
            if path == "/upload/image":
                return self._json(200, {"name": "uploaded.png", "subfolder": "", "type": "input"})
            if path == "/prompt":
                request = json.loads(body)
                prompt_id = f"p{len(state.comfy_prompts) + 1}"
                state.comfy_prompts[prompt_id] = request["prompt"]
                return self._json(200, {"prompt_id": prompt_id, "number": 1})
            self._json(404, {"error": "not found"})

    return Handler


@pytest.fixture()
def fake(tmp_path, monkeypatch):
    state = FakeState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    state.port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("LOCALAI_SERVER", "127.0.0.1")
    monkeypatch.setenv("LOCALAI_PORTAL_PORT", str(state.port))
    monkeypatch.setenv("LOCALAI_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mcp, "SERVICE_POLL_SEC", 0.01)
    monkeypatch.setattr(mcp, "COMFY_POLL_SEC", 0.01)
    yield state
    server.shutdown()
    server.server_close()


def call(name: str, arguments: dict[str, Any]) -> tuple[bool, Any]:
    result = mcp.call_tool(name, arguments)
    text = result["content"][0]["text"]
    if result["isError"]:
        return True, text
    return False, json.loads(text)


# ------------------------------------------------------------------------------------------------
# 純粋関数

def test_exclusive_keys_match_portal():
    portal = load_portal()
    assert tuple(mcp.GPU_EXCLUSIVE_KEYS) == tuple(portal.GPU_EXCLUSIVE_KEYS)
    assert tuple(mcp.OLLAMA_UNLOAD_BEFORE_START_KEYS) == tuple(portal.OLLAMA_UNLOAD_BEFORE_START_KEYS)
    portal_keys = {service["key"] for service in portal.SERVICES}
    assert set(mcp.STRATA_KEYS) <= portal_keys
    assert set(mcp.GRADIO_KEYS) <= portal_keys


@pytest.mark.parametrize("raw, expected", [
    ("192.168.1.10", "192.168.1.10"),
    ("http://192.168.1.10:8080/", "192.168.1.10"),
    ("192.168.1.10:8080", "192.168.1.10"),
    (" server.local ", "server.local"),
])
def test_server_host(monkeypatch, raw, expected):
    monkeypatch.setenv("LOCALAI_SERVER", raw)
    assert mcp.server_host() == expected


def test_server_host_missing(monkeypatch):
    monkeypatch.delenv("LOCALAI_SERVER", raising=False)
    with pytest.raises(mcp.ToolError):
        mcp.server_host()


def test_start_side_effects():
    status = {
        "ollama": {"models": [{"name": "qwen3.8:27b"}]},
        "services": [
            {"key": "stable-audio", "state": "running"}, {"key": "audiocraft", "state": "stopped"},
            {"key": "comfyui", "state": "starting"}, {"key": "open-webui", "state": "running"},
        ],
    }
    effects = mcp.start_side_effects("audiocraft", status)
    assert effects == {"stop_services": ["comfyui", "stable-audio"], "unload_ollama_models": ["qwen3.8:27b"]}
    # Open WebUI は GPU を使わないので何も止めない
    assert mcp.start_side_effects("open-webui", status) == {"stop_services": [], "unload_ollama_models": []}
    # ComfyUI は Ollama を降ろさない
    assert mcp.start_side_effects("comfyui", status)["unload_ollama_models"] == []


def test_build_gradio_data():
    parameters = GRADIO_INFO["named_endpoints"]["/run_voice_design"]["parameters"]
    assert mcp.build_gradio_data(parameters, {"text": "a", "design": "b"}) == ["a", "Auto", "b"]
    with pytest.raises(mcp.ToolError, match="無い引数"):
        mcp.build_gradio_data(parameters, {"txt": "a"})


def test_parse_sse():
    assert mcp.parse_sse(iter([b"event: generating\n", b"data: [1]\n", b"event: complete\n", b"data: [2]\n"])) == ("complete", [2])
    assert mcp.parse_sse(iter([b"event: error\n", b"data: null\n"])) == ("error", None)
    with pytest.raises(mcp.ToolError):
        mcp.parse_sse(iter([b"event: heartbeat\n", b"data: null\n"]))


def test_collect_and_strip_file_data():
    file_data = {"path": "/a.glb", "meta": {"_type": "gradio.FileData"}}
    result = [file_data, {"value": {"path": "/b.wav", "url": "x"}}, "text", list(range(15))]
    assert [item["path"] for item in mcp.collect_file_data(result)] == ["/a.glb", "/b.wav"]
    stripped = mcp.strip_file_data(result)
    assert stripped[0] == "<file>" and stripped[1] == {"value": "<file>"} and stripped[2] == "text"
    assert len(stripped[3]) == mcp.RESULT_LIST_LIMIT + 1


def test_unique_output_path(tmp_path):
    first = mcp.unique_output_path(tmp_path, "a b/c", ".wav")
    first.write_bytes(b"1")
    second = mcp.unique_output_path(tmp_path, "a b/c", ".wav")
    assert first != second and first.name.endswith("_a_b_c.wav") and "/" not in first.name


def test_ui_workflow_detection_and_overrides():
    assert mcp.is_ui_workflow({"nodes": [], "links": []})
    assert not mcp.is_ui_workflow({"3": {"class_type": "KSampler", "inputs": {}}})
    prompt = {"3": {"class_type": "KSampler", "inputs": {"seed": 1}}}
    assert mcp.apply_overrides(prompt, {"3.seed": 5}) == ["3.seed"]
    assert prompt["3"]["inputs"]["seed"] == 5
    with pytest.raises(mcp.ToolError):
        mcp.apply_overrides(prompt, {"99.seed": 1})
    with pytest.raises(mcp.ToolError):
        mcp.apply_overrides(prompt, {"seed": 1})


def test_choose_mesh_endpoint():
    assert mcp.choose_mesh_endpoint(GRADIO_INFO, "pixal3d", False, ["front_image"]) == "/_generate"
    info = {"named_endpoints": {"/other": {"parameters": [{"parameter_name": "mv_image_front"}]}}}
    assert mcp.choose_mesh_endpoint(info, "hunyuan3d", False, ["mv_image_front"]) == "/other"
    with pytest.raises(mcp.ToolError):
        mcp.choose_mesh_endpoint({"named_endpoints": {}}, "hunyuan3d", True, ["mv_image_front"])


def test_multipart():
    body, content_type = mcp.build_multipart([("overwrite", "true")], [("image", "a.png", b"DATA")])
    boundary = content_type.split("boundary=")[1]
    assert body.startswith(f"--{boundary}\r\n".encode()) and body.endswith(f"--{boundary}--\r\n".encode())
    assert b'name="image"; filename="a.png"' in body and b"DATA" in body and b"image/png" in body


# ------------------------------------------------------------------------------------------------
# MCP の往復

def run_mcp(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reader = io.BytesIO(b"".join((json.dumps(message) + "\n").encode() for message in messages))
    writer = io.BytesIO()
    mcp.McpServer(reader, writer).serve()
    return [json.loads(line) for line in writer.getvalue().decode().splitlines()]


def test_mcp_handshake_and_call(fake):
    replies = run_mcp([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "localai_status", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "nope"},
        {"jsonrpc": "2.0", "id": 5, "method": "ping"},
    ])
    by_id = {reply.get("id"): reply for reply in replies}
    assert set(by_id) == {1, 2, 3, 4, 5}  # 通知には応えない
    assert by_id[1]["result"]["protocolVersion"] == "2025-03-26"
    names = {tool["name"] for tool in by_id[2]["result"]["tools"]}
    assert {"localai_status", "localai_service", "localai_ollama", "localai_chat", "localai_music", "localai_sound", "localai_stable_audio",
            "localai_tts", "localai_mesh", "localai_comfyui", "localai_comfyui_result",
            "localai_gradio_info", "localai_gradio_call"} == names
    for tool in by_id[2]["result"]["tools"]:
        assert tool["inputSchema"]["type"] == "object" and "handler" not in tool
    status = json.loads(by_id[3]["result"]["content"][0]["text"])
    assert len(status["services"]) == 12
    assert by_id[4]["error"]["code"] == -32601
    assert by_id[5]["result"] == {}


def test_mcp_unknown_protocol_falls_back():
    replies = run_mcp([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}}])
    assert replies[0]["result"]["protocolVersion"] == mcp.SUPPORTED_PROTOCOL_VERSIONS[0]


def test_mcp_parse_error():
    reader = io.BytesIO(b"not json\n")
    writer = io.BytesIO()
    mcp.McpServer(reader, writer).serve()
    assert json.loads(writer.getvalue())["error"]["code"] == -32700


# ------------------------------------------------------------------------------------------------
# 起動・停止

def test_service_refuses_when_it_would_stop_others(fake):
    fake.states["stable-audio"] = "running"
    fake.ollama_models = ["qwen3.8:27b"]
    error, result = call("localai_service", {"key": "audiocraft", "action": "start"})
    assert not error and result["ok"] is False
    assert [item["key"] for item in result["would_stop"]] == ["stable-audio"]
    assert result["would_unload_ollama_models"] == ["qwen3.8:27b"]
    assert fake.states["audiocraft"] == "stopped"
    assert not any(method == "POST" for method, *_ in fake.requests)


def test_service_start_with_stop_others_and_wait(fake):
    fake.states["stable-audio"] = "running"
    error, result = call("localai_service", {"key": "audiocraft", "action": "start", "stop_others": True})
    assert not error and result["ok"] and result["state"] == "running" and result["stopped"] == ["stable-audio"]
    post = [request for request in fake.requests if request[0] == "POST"][0]
    assert post[1] == "/api/service/audiocraft/start?wait=900" and post[2].get("X-Portal-Request") == "1"


def test_service_start_free_and_stop(fake):
    error, result = call("localai_service", {"key": "open-webui", "action": "start"})
    assert not error and result["ok"] and result["state"] == "running"
    error, result = call("localai_service", {"key": "open-webui", "action": "start"})
    assert "既に稼働中" in result["message"]
    error, result = call("localai_service", {"key": "open-webui", "action": "stop"})
    assert not error and result["state"] == "stopped"
    error, result = call("localai_service", {"key": "open-webui", "action": "stop"})
    assert "既に停止" in result["message"]


def test_service_start_uses_portal_wait(fake):
    error, result = call("localai_service", {"key": "strata", "action": "start", "timeout_sec": 900})
    assert not error and result["ok"] and result["state"] == "running" and result["waited_sec"] >= 0
    posts = [request[1] for request in fake.requests if request[0] == "POST"]
    assert posts == ["/api/service/strata/start?wait=900"]
    # 入口ページが待ったので、MCP 側から /api/status を問い合わせ続けていない（起動前の 1 回だけ）
    assert sum(1 for request in fake.requests if request[1] == "/api/status") == 1


def test_service_start_wait_is_capped_at_portal_limit(fake):
    error, result = call("localai_service", {"key": "open-webui", "action": "start", "timeout_sec": 99999})
    assert not error and result["ok"]
    assert [request[1] for request in fake.requests if request[0] == "POST"] == [
        f"/api/service/open-webui/start?wait={mcp.PORTAL_WAIT_MAX_SEC}"
    ]


def test_service_start_reports_failure_from_portal(fake):
    fake.start_fails = True
    error, result = call("localai_service", {"key": "open-webui", "action": "start"})
    assert not error and result["ok"] is False and result["state"] == "stopped"
    assert "journalctl" in result["message"]


def test_service_start_falls_back_on_old_portal(fake):
    fake.old_portal = True
    error, result = call("localai_service", {"key": "open-webui", "action": "start"})
    assert not error and result["ok"] and result["state"] == "running"
    posts = [request[1] for request in fake.requests if request[0] == "POST"]
    assert posts == ["/api/service/open-webui/start?wait=900", "/api/service/open-webui/start"]


def test_service_start_without_wait_does_not_send_wait(fake):
    error, result = call("localai_service", {"key": "open-webui", "action": "start", "wait": False})
    assert not error and result["state"] == "starting"
    assert [request[1] for request in fake.requests if request[0] == "POST"] == ["/api/service/open-webui/start"]


def test_ollama_list(fake):
    fake.ollama_models = ["qwen3.8:27b"]
    error, result = call("localai_ollama", {})
    assert not error
    assert result["installed"] == ["qwen3.8:27b", "gemma4:e4b"]
    assert result["loaded"] == [{"name": "qwen3.8:27b", "size_vram_mib": 22201}]


def test_ollama_load_refuses_when_it_would_stop_others(fake):
    fake.states["strata"] = "running"
    error, result = call("localai_ollama", {"action": "load", "model": "qwen3.8:27b"})
    assert not error and result["ok"] is False
    assert [item["key"] for item in result["would_stop"]] == ["strata"]
    assert not any(method == "POST" for method, *_ in fake.requests)
    assert fake.states["strata"] == "running"


def test_ollama_load_with_stop_others_and_keep_alive(fake):
    fake.states["strata"] = "running"
    error, result = call("localai_ollama", {"action": "load", "model": "qwen3.8:27b", "keep_alive": "30m", "stop_others": True})
    assert not error and result["ok"] and result["stopped"] == ["strata"]
    assert result["keep_alive_seen"] == "30m" and "Open WebUI" in result["note"]
    post = [request for request in fake.requests if request[0] == "POST"][0]
    assert post[1] == "/api/ollama/qwen3.8:27b/load?keep_alive=30m" and post[2].get("X-Portal-Request") == "1"
    assert fake.ollama_models == ["qwen3.8:27b"]


def test_ollama_unload_and_unknown_model(fake):
    fake.ollama_models = ["qwen3.8:27b"]
    error, result = call("localai_ollama", {"action": "unload", "model": "qwen3.8:27b"})
    assert not error and result["ok"] and fake.ollama_models == []
    error, text = call("localai_ollama", {"action": "load", "model": "nosuch:1b"})
    assert error and "gemma4:e4b" in text


def test_ollama_rejects_bad_input_and_old_portal(fake):
    assert call("localai_ollama", {"action": "pull", "model": "qwen3.8:27b"})[0]
    assert call("localai_ollama", {"action": "load"})[0]
    fake.old_portal = True
    error, text = call("localai_ollama", {})
    assert error and "入口ページが古く" in text


def test_service_rejects_bad_input(fake):
    assert call("localai_service", {"key": "portal", "action": "stop"})[0]
    assert call("localai_service", {"key": "nope", "action": "start"})[0]
    assert call("localai_service", {"key": "strata", "action": "restart"})[0]


def test_unreachable_server(monkeypatch):
    monkeypatch.setenv("LOCALAI_SERVER", "127.0.0.1")
    monkeypatch.setenv("LOCALAI_PORTAL_PORT", "1")
    error, text = call("localai_status", {})
    assert error and "ufw" in text


# ------------------------------------------------------------------------------------------------
# 生成

def test_chat(fake):
    error, text = call("localai_chat", {"model": "strata-swift", "prompt": "1+1"})
    assert error and "localai_service" in text  # 止まっているときは起動を促す
    fake.states["strata-swift"] = "running"
    error, result = call("localai_chat", {"model": "strata-swift", "prompt": "1+1", "system": "短く", "max_tokens": 10,
                                          "save_to_file": True})
    assert not error and result["content"] == "答え: 1+1" and result["reasoning_chars"] == 3
    assert "reasoning" not in result and Path(result["saved"]).is_file()
    request = json.loads([r for r in fake.requests if r[1] == "/v1/chat/completions"][0][3])
    assert request["model"] == "strata-swift:IQ3_XXS" and request["messages"][0]["role"] == "system"
    assert request["max_tokens"] == 10 and request["stream"] is False


def test_chat_rejects_unknown_model(fake):
    assert call("localai_chat", {"model": "hunyuan3d", "prompt": "x"})[0]


def test_music_with_source_audio(fake, tmp_path):
    fake.states["audiocraft"] = "running"
    source = tmp_path / "hum.wav"
    source.write_bytes(b"HUM")
    error, result = call("localai_music", {"prompt": "piano", "duration": 5, "mode": "melody", "audio_path": "hum.wav"})
    assert not error, result
    assert Path(result["saved"]).read_bytes() == WAV_BYTES and result["server_info"]["seed"] == "42"
    request = json.loads([r for r in fake.requests if r[1] == "/api/v1/music"][0][3])
    assert base64.b64decode(request["audio_base64"]) == b"HUM" and "audio_path" not in request
    error, text = call("localai_music", {"prompt": "piano", "duration": 99})
    assert error and "範囲外" in text
    assert call("localai_music", {"prompt": "piano", "audio_path": "missing.wav"})[0]


def test_sound_and_stable_audio(fake):
    fake.states["audiocraft"] = "running"
    fake.states["stable-audio"] = "running"
    assert not call("localai_sound", {"prompt": "steps", "duration": 3})[0]
    error, result = call("localai_stable_audio", {"prompt": "beat", "duration": 10, "steps": 8})
    assert not error and result["server_info"]["seed"] == "42"


def test_tts(fake):
    fake.states["qwen3-tts"] = "running"
    error, result = call("localai_tts", {"text": "こんにちは", "design": "明るい声"})
    assert not error, result
    assert fake.gradio_calls["run_voice_design"] == ["こんにちは", "Auto", "明るい声"]
    assert Path(result["saved"][0]).read_bytes() == WAV_BYTES and result["saved"][0].endswith(".wav")
    # 応答の URL のホスト（other-host）ではなく、LOCALAI_SERVER へ取りに行く
    assert any(request[1].startswith("/gradio_api/file=") for request in fake.requests)
    assert call("localai_tts", {"text": "x"})[0]


def test_gradio_error(fake):
    fake.states["qwen3-tts"] = "running"
    fake.gradio_fail = True
    error, text = call("localai_tts", {"text": "a", "design": "b"})
    assert error and "boom" in text


def test_mesh_pixal3d(fake, tmp_path):
    fake.states["pixal3d"] = "running"
    (tmp_path / "front.png").write_bytes(PNG_BYTES)
    (tmp_path / "left.png").write_bytes(PNG_BYTES)
    error, result = call("localai_mesh", {"engine": "pixal3d", "front": "front.png", "left": "left.png",
                                          "options": {"seed": 7}})
    assert not error, result
    data = fake.gradio_calls["_generate"]
    assert data[0]["path"] == fake.uploads[0] and data[0]["meta"]["_type"] == "gradio.FileData"
    assert data[1] is None and data[2] is None and data[3]["path"] == fake.uploads[1] and data[4] == 7
    assert Path(result["saved"][0]).read_bytes() == GLB_BYTES and result["saved"][0].endswith(".glb")
    assert result["outputs"][1].endswith("先頭だけ）") and len(result["outputs"][2]) == mcp.RESULT_LIST_LIMIT + 1
    assert call("localai_mesh", {"engine": "pixal3d", "left": "left.png"})[0]  # 正面が要る
    assert call("localai_mesh", {"engine": "pixal3d", "front": "front.png", "options": {"bogus": 1}})[0]


def test_gradio_info_and_call(fake, tmp_path):
    fake.states["pixal3d"] = "running"
    error, result = call("localai_gradio_info", {"key": "pixal3d"})
    assert not error and result["endpoints"]["/_generate"][0]["file"] is True
    (tmp_path / "f.png").write_bytes(PNG_BYTES)
    error, result = call("localai_gradio_call", {"key": "pixal3d", "api_name": "_generate",
                                                 "files": {"front_image": "f.png"}, "args": {"seed": 3}})
    assert not error and result["api_name"] == "/_generate"
    assert call("localai_gradio_call", {"key": "pixal3d", "api_name": "/missing"})[0]
    assert call("localai_gradio_info", {"key": "strata"})[0]


def test_comfyui(fake, tmp_path):
    fake.states["comfyui"] = "running"
    workflow = {"3": {"class_type": "KSampler", "inputs": {"seed": 1}}, "10": {"class_type": "LoadImage", "inputs": {"image": "x"}}}
    (tmp_path / "wf.json").write_text(json.dumps(workflow))
    (tmp_path / "in.png").write_bytes(PNG_BYTES)
    error, result = call("localai_comfyui", {"workflow_path": "wf.json", "image_inputs": {"10.image": "in.png"},
                                             "set": {"3.seed": 99}})
    assert not error, result
    sent = fake.comfy_prompts[result["prompt_id"]]
    assert sent["3"]["inputs"]["seed"] == 99 and sent["10"]["inputs"]["image"] == "uploaded.png"
    assert result["status"] == "success" and len(result["saved"]) == 1  # temp は既定で落とさない
    assert Path(result["saved"][0]).read_bytes() == PNG_BYTES


def test_comfyui_async_and_ui_format(fake, tmp_path):
    fake.states["comfyui"] = "running"
    fake.comfy_done = False
    error, result = call("localai_comfyui", {"workflow": {"3": {"class_type": "X", "inputs": {}}}, "wait": False})
    assert not error and result["status"] == "queued"
    error, pending = call("localai_comfyui_result", {"prompt_id": result["prompt_id"], "wait": False})
    assert not error and pending["status"] == "pending"
    fake.comfy_done = True
    error, done = call("localai_comfyui_result", {"prompt_id": result["prompt_id"], "include_temp": True})
    assert not error and done["status"] == "success" and len(done["saved"]) == 2
    error, text = call("localai_comfyui", {"workflow": {"nodes": [], "links": []}})
    assert error and "Export (API)" in text


def test_comfyui_saved_workflow(fake):
    fake.states["comfyui"] = "running"
    error, result = call("localai_comfyui", {"workflow_name": "VFX/Test_api.json", "set": {"6.text": "mist"}})
    assert not error, result
    assert fake.comfy_prompts[result["prompt_id"]]["6"]["inputs"]["text"] == "mist"
    assert any(path == "/api/userdata/workflows%2FVFX%2FTest_api.json" for _m, path, _h, _b in fake.requests)
    error, text = call("localai_comfyui", {"workflow_name": "VFX/Missing_api.json"})
    assert error and "ありません" in text
    for bad in ("../secret.json", "/etc/x.json", "VFX/a.txt"):
        assert call("localai_comfyui", {"workflow_name": bad})[0]


def test_check_command(fake, capsys):
    fake.states["open-webui"] = "running"
    assert mcp.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "OK: 入口ページ" in out and "OK open-webui" in out
