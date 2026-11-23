"""REST の口（saudio.rest）の試験。偽の Engine を使い、torch・gradio・上流・GPU は要らない。

fastapi と httpx（TestClient が使う）が要る。無ければ飛ばす。
実行: uvx --with pytest --with fastapi --with httpx pytest tools/stable-audio/tests/test_rest.py -q
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import sys
import threading
import wave
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

from saudio import config, params, rest  # noqa: E402
from saudio.engine import GenerationResult  # noqa: E402

JSON_HEADERS = {"Content-Type": "application/json"}


class FakeEngine:
    """Engine の代わり。実際の生成はせず、無音のステレオ wav と json を outputs_dir に書く。"""

    def __init__(self, outputs_dir: Path, loaded: bool = True) -> None:
        self.outputs_dir = outputs_dir
        self.model_id = "fake/stable-audio-3-medium"
        self.device = "cpu"
        self.loaded = loaded
        self.requests: list[params.GenerationParams] = []
        self.error: Exception | None = None
        self.block = False
        self.entered = threading.Event()
        self.release = threading.Event()

    def generate(self, request, out_path=None, now=None):
        self.requests.append(request)
        self.entered.set()
        if self.block:
            assert self.release.wait(timeout=10), "試験が release を呼ばなかった"
        if self.error is not None:
            raise self.error
        if request.source is not None and request.source.data.startswith(b"BAD"):
            raise params.ParamError("元の音を音声として読めません（偽）")
        seed = params.resolve_seed(request.seed)
        created_at = now or dt.datetime(2026, 11, 23, 12, 0, 0)
        stem = params.unique_stem(self.outputs_dir, params.build_output_stem(request.mode, seed, created_at))
        wav_path, meta_path = params.output_paths(self.outputs_dir, stem)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        rate = 8000  # 試験を軽くするため小さいサンプルレートで書く（メタデータは本物の値）
        frames = int(request.duration * rate)
        with wave.open(str(wav_path), "wb") as wav_file:
            wav_file.setnchannels(2)
            wav_file.setsampwidth(2)
            wav_file.setframerate(rate)
            wav_file.writeframes(b"\x00\x00\x00\x00" * frames)
        metadata = params.build_metadata(
            request, resolved_seed=seed, model_id=self.model_id, sample_rate=params.SAMPLE_RATE, channels=2,
            samples=int(request.duration * params.SAMPLE_RATE), elapsed_sec=0.5, peak_vram_mib=None, device="cpu",
            wav_name=wav_path.name, now=created_at,
        )
        meta_path.write_text(json.dumps(metadata), encoding="utf-8")
        source_path = None
        if request.source is not None:
            source_path = wav_path.with_name(wav_path.stem + "-source.wav")
            source_path.write_bytes(request.source.data)
        return GenerationResult(wav_path=wav_path, meta_path=meta_path, metadata=metadata, source_path=source_path)


@pytest.fixture()
def fake_engine(tmp_path: Path) -> FakeEngine:
    return FakeEngine(tmp_path / "outputs")


@pytest.fixture()
def client(fake_engine: FakeEngine) -> TestClient:
    return TestClient(rest.build_app(fake_engine))


def post(client: TestClient, body, headers=None):
    return client.post("/api/v1/generate", content=json.dumps(body), headers=headers or JSON_HEADERS)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_text_returns_wav_and_headers(client: TestClient, fake_engine: FakeEngine) -> None:
    response = post(client, {"prompt": "calm piano", "duration": 3, "seed": 5})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "audio/wav" and response.content[:4] == b"RIFF"
    assert response.headers["X-Saudio-Seed"] == "5" and response.headers["X-Saudio-Mode"] == "text"
    assert response.headers["X-Saudio-Sample-Rate"] == "44100" and response.headers["X-Saudio-Channels"] == "2"
    assert (fake_engine.outputs_dir / response.headers["X-Saudio-File"]).is_file()
    assert fake_engine.requests[0].duration == 3.0


def test_defaults_are_applied(client: TestClient, fake_engine: FakeEngine) -> None:
    assert post(client, {"prompt": "x"}).status_code == 200
    request = fake_engine.requests[0]
    assert request.duration == params.DEFAULT_DURATION and request.steps == params.DEFAULT_STEPS and request.mode == "text"


@pytest.mark.parametrize(
    "body, mode",
    [
        ({"mode": "audio", "init_noise_level": 0.3}, "audio"),
        ({"mode": "inpaint", "inpaint_start": 1}, "inpaint"),
        ({"mode": "inpaint", "inpaint_start": 1, "inpaint_end": 2}, "inpaint"),
    ],
)
def test_source_modes(client: TestClient, fake_engine: FakeEngine, body, mode) -> None:
    response = post(client, {"prompt": "x", "duration": 3, "audio_base64": _b64(b"RIFFsrc"), **body})
    assert response.status_code == 200, response.text
    request = fake_engine.requests[0]
    assert request.mode == mode and request.source.data == b"RIFFsrc"
    assert response.headers["X-Saudio-Mode"] == mode


def test_keep_false_removes_all_files(client: TestClient, fake_engine: FakeEngine) -> None:
    body = {"prompt": "x", "duration": 2, "mode": "audio", "audio_base64": _b64(b"RIFF"), "keep": False}
    response = post(client, body)
    assert response.status_code == 200 and "X-Saudio-File" not in response.headers
    assert list(fake_engine.outputs_dir.iterdir()) == []


def test_health(client: TestClient) -> None:
    payload = client.get("/api/v1/health").json()
    assert payload["status"] == "ok" and payload["loaded"] is True and payload["model"] == "fake/stable-audio-3-medium"
    assert payload["sample_rate"] == 44100 and payload["channels"] == 2
    assert payload["duration_sec"] == {"min": 1.0, "max": 380.0, "default": 30.0}
    assert payload["modes"] == ["text", "audio", "inpaint"] and payload["max_pending"] == config.REST_MAX_PENDING


@pytest.mark.parametrize(
    "body, fragment",
    [
        ({}, "prompt"),
        ({"prompt": ""}, "説明文が空"),
        ({"prompt": "x", "duration": 400}, "380 秒以下"),
        ({"prompt": "x", "mode": "audio"}, "元の音"),
        ({"prompt": "x", "audio_base64": _b64(b"RIFF")}, "モードが text"),
        ({"prompt": "x", "mode": "inpaint", "audio_base64": _b64(b"RIFF")}, "開始"),
        ({"prompt": "x", "mode": "audio", "audio_base64": "@@"}, "base64"),
        ({"prompt": "x", "cfg_scale": 7}, "未知の項目があります: cfg_scale"),
        ([1], "オブジェクト"),
    ],
)
def test_bad_bodies_return_400(client: TestClient, fake_engine: FakeEngine, body, fragment) -> None:
    response = post(client, body)
    assert response.status_code == 400 and fragment in response.json()["error"]
    assert fake_engine.requests == []


def test_unreadable_source_from_engine_is_400(client: TestClient) -> None:
    response = post(client, {"prompt": "x", "mode": "audio", "audio_base64": _b64(b"BAD")})
    assert response.status_code == 400 and "読めません" in response.json()["error"]


def test_invalid_json_and_content_types(client: TestClient) -> None:
    assert client.post("/api/v1/generate", content="{", headers=JSON_HEADERS).status_code == 400
    assert client.post("/api/v1/generate", content="prompt=x", headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 415
    assert client.post("/api/v1/generate", content=b"{}").status_code == 415
    ok = client.post("/api/v1/generate", content=json.dumps({"prompt": "x", "duration": 1}), headers={"Content-Type": "application/json; charset=utf-8"})
    assert ok.status_code == 200


def test_get_is_405_and_docs_are_hidden(client: TestClient) -> None:
    response = client.get("/api/v1/generate")
    assert response.status_code == 405 and response.headers["allow"] == "POST"
    for path in ("/docs", "/openapi.json", "/redoc"):
        assert client.get(path).status_code == 404


def test_not_loaded_is_503(tmp_path: Path) -> None:
    client = TestClient(rest.build_app(FakeEngine(tmp_path, loaded=False)))
    assert post(client, {"prompt": "x"}).status_code == 503


def test_engine_failure_is_500_and_gate_is_released(fake_engine: FakeEngine) -> None:
    gate = rest.PendingGate(1)
    client = TestClient(rest.build_app(fake_engine, gate))
    fake_engine.error = RuntimeError("boom")
    response = post(client, {"prompt": "x", "duration": 1})
    assert response.status_code == 500 and "RuntimeError: boom" in response.json()["error"]
    assert gate.pending == 0


def test_full_gate_returns_503_with_retry_after(fake_engine: FakeEngine) -> None:
    gate = rest.PendingGate(1)
    client = TestClient(rest.build_app(fake_engine, gate))
    fake_engine.block = True
    results = {}

    def first() -> None:
        results["first"] = post(client, {"prompt": "x", "duration": 1})

    thread = threading.Thread(target=first)
    thread.start()
    assert fake_engine.entered.wait(timeout=10)
    busy = post(client, {"prompt": "y", "duration": 1})
    assert busy.status_code == 503 and busy.headers["Retry-After"] == str(config.REST_RETRY_AFTER_SEC)
    fake_engine.release.set()
    thread.join(timeout=10)
    assert results["first"].status_code == 200 and gate.pending == 0


def test_install_routes_go_before_a_catch_all(fake_engine: FakeEngine) -> None:
    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def catch_all(path: str):
        return {"catch_all": path}

    rest.install_routes(app, fake_engine)
    client = TestClient(app)
    assert client.get("/api/v1/health").json()["status"] == "ok"
    assert client.get("/other").json() == {"catch_all": "other"}
