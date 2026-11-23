"""REST の口（acraft.rest）の試験。偽の Engine を使い、torch・gradio・audiocraft・GPU は要らない。

fastapi と httpx（TestClient が使う）が要る。無ければ飛ばす。
実行: uvx --with pytest --with fastapi --with httpx pytest tools/audiocraft/tests/test_rest.py -q
（./localai test --only audiocraft は、この 2 つを足して走らせる）
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

from fastapi.testclient import TestClient  # noqa: E402

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

from acraft import config, params, rest  # noqa: E402
from acraft.engine import GenerationResult  # noqa: E402

JSON_HEADERS = {"Content-Type": "application/json"}


class FakeEngine:
    """Engine の代わり。実際の生成はせず、無音の wav と json を outputs_dir に書く。"""

    def __init__(self, outputs_dir: Path, loaded: tuple[str, ...] = params.KINDS, melody: bool = True) -> None:
        self.outputs_dir = outputs_dir
        self.model_ids = {params.KIND_MUSIC: "fake/musicgen", params.KIND_SOUND: "fake/audiogen"}
        self.melody_model_id = "fake/musicgen-melody" if melody else None
        self.device = "cpu"
        self._loaded = loaded
        self.requests: list[params.GenerationParams] = []
        self.error: Exception | None = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = False

    def loaded_kinds(self) -> tuple[str, ...]:
        return self._loaded

    def available_modes(self, kind: str) -> tuple[str, ...]:
        if kind not in self._loaded:
            return ()
        return tuple(m for m in params.KIND_MODES[kind] if m != params.MODE_MELODY or self.melody_model_id)

    def generate(self, request, out_path=None, now=None):
        self.requests.append(request)
        self.entered.set()
        if self.block:
            assert self.release.wait(timeout=10), "試験が release を呼ばなかった"
        if self.error is not None:
            raise self.error
        source_path = None
        if request.source is not None:
            # 本物の Engine は soundfile で読む。偽物は中身の先頭で「読めない音」を表す。
            if request.source.data.startswith(b"BAD"):
                raise params.ParamError("元の音を音声として読めません（偽）")
        spec = params.KIND_SPECS[request.kind]
        seed = params.resolve_seed(request.seed)
        created_at = now or dt.datetime(2026, 10, 27, 12, 0, 0)
        stem = params.unique_stem(self.outputs_dir, params.build_output_stem(request.kind, seed, created_at, request.mode))
        wav_path, meta_path = params.output_paths(self.outputs_dir, stem)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        frames = int(request.duration * spec.sample_rate)
        with wave.open(str(wav_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(spec.sample_rate)
            wav_file.writeframes(b"\x00\x00" * frames)
        metadata = params.build_metadata(
            request, resolved_seed=seed, model_id=self.model_ids[request.kind], sample_rate=spec.sample_rate,
            samples=frames, elapsed_sec=1.234, peak_vram_mib=None, device="cpu", wav_name=wav_path.name, now=created_at,
        )
        meta_path.write_text(json.dumps(metadata), encoding="utf-8")
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


def post(client: TestClient, path: str, body, headers=None):
    return client.post(path, content=json.dumps(body), headers=headers or JSON_HEADERS)


# --------------------------------------------------------------------------
# 正常系
# --------------------------------------------------------------------------


def test_music_returns_wav_bytes_with_metadata_headers(client: TestClient, fake_engine: FakeEngine) -> None:
    response = post(client, "/api/v1/music", {"prompt": "calm piano", "duration": 2, "seed": 42})
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content[:4] == b"RIFF" and response.content[8:12] == b"WAVE"
    assert response.headers["x-acraft-kind"] == "music"
    assert response.headers["x-acraft-model"] == "fake/musicgen"
    assert response.headers["x-acraft-seed"] == "42"
    assert response.headers["x-acraft-sample-rate"] == "32000"
    assert response.headers["x-acraft-duration-sec"] == "2.0"
    assert response.headers["x-acraft-elapsed-sec"] == "1.23"
    filename = response.headers["x-acraft-file"]
    assert filename.endswith("-music-42.wav")
    assert response.headers["content-disposition"] == f'attachment; filename="{filename}"'
    # 既定の keep=true なので、サーバーにも残る
    assert (fake_engine.outputs_dir / filename).is_file()
    assert (fake_engine.outputs_dir / filename).with_suffix(".json").is_file()


def test_wav_body_is_exactly_what_the_engine_wrote(client: TestClient, fake_engine: FakeEngine) -> None:
    response = post(client, "/api/v1/sound", {"prompt": "dog barking", "duration": 3, "seed": 1})
    written = (fake_engine.outputs_dir / response.headers["x-acraft-file"]).read_bytes()
    assert response.content == written
    with wave.open(str(fake_engine.outputs_dir / response.headers["x-acraft-file"])) as wav_file:
        assert (wav_file.getframerate(), wav_file.getnchannels(), wav_file.getnframes()) == (16000, 1, 48000)


def test_keep_false_removes_files_and_omits_file_header(client: TestClient, fake_engine: FakeEngine) -> None:
    response = post(client, "/api/v1/music", {"prompt": "calm piano", "duration": 1, "keep": False})
    assert response.status_code == 200 and response.content[:4] == b"RIFF"
    assert "x-acraft-file" not in response.headers
    assert list(fake_engine.outputs_dir.iterdir()) == []


def test_omitted_fields_use_the_defaults_of_each_kind(client: TestClient, fake_engine: FakeEngine) -> None:
    post(client, "/api/v1/music", {"prompt": "calm piano"})
    post(client, "/api/v1/sound", {"prompt": "dog barking", "duration": None, "top_k": None})
    music, sound = fake_engine.requests
    assert (music.kind, music.duration, music.seed, music.top_k, music.cfg_coef) == ("music", 10.0, -1, 250, 3.0)
    assert (sound.kind, sound.duration, sound.top_k) == ("sound", 5.0, 250)


def test_prompt_whitespace_is_cleaned_like_the_screen(client: TestClient, fake_engine: FakeEngine) -> None:
    post(client, "/api/v1/music", {"prompt": "  calm   piano\nmelody ", "duration": 1})
    assert fake_engine.requests[0].prompt == "calm piano melody"


def test_content_type_with_charset_is_accepted(client: TestClient) -> None:
    response = post(client, "/api/v1/music", {"prompt": "x", "duration": 1}, headers={"Content-Type": "application/json; charset=utf-8"})
    assert response.status_code == 200


def test_health_reports_models_limits_and_pending(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok" and payload["device"] == "cpu"
    assert payload["loaded"] == ["music", "sound"] and payload["pending"] == 0
    assert payload["max_pending"] == config.REST_MAX_PENDING
    assert payload["kinds"]["music"] == {
        "model": "fake/musicgen", "sample_rate": 32000, "duration_sec": {"min": 1.0, "max": 30.0, "default": 10.0},
        "modes": ["text", "continuation", "melody"],
    }
    assert payload["kinds"]["sound"]["modes"] == ["text", "continuation"]
    assert payload["melody_model"] == "fake/musicgen-melody"
    assert payload["source_audio"]["max_bytes"] == params.SOURCE_MAX_BYTES
    assert payload["kinds"]["sound"]["sample_rate"] == 16000
    assert payload["kinds"]["sound"]["duration_sec"]["max"] == 20.0


# --------------------------------------------------------------------------
# 入力の誤り（400・415）と、受け付けない状態（503）、失敗（500）
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body, fragment",
    [
        ({}, "prompt"),
        ({"prompt": None}, "prompt"),
        ({"prompt": ""}, "説明文が空"),
        ({"prompt": "x", "duration": 31}, "30 秒以下"),
        ({"prompt": "x", "duration": "10"}, "長さ は数"),
        ({"prompt": "x", "seed": 1.5}, "整数"),
        ({"prompt": "x", "top_k": 0, "top_p": 0}, "両方 0"),
        ({"prompt": "x", "keep": "no"}, "keep は true か false"),
        ({"prompt": "x", "promt": "typo"}, "未知の項目があります: promt"),
        ({"prompt": "x", "mode": "melody"}, "元の音"),
        ({"prompt": "x", "mode": "remix"}, "モード"),
        ({"prompt": "x", "audio_base64": base64.b64encode(b"RIFF").decode()}, "モードが text"),
        ({"prompt": "x", "mode": "continuation", "audio_base64": "@@@"}, "base64 として読めません"),
        ({"prompt": "x", "mode": "continuation", "audio_base64": ""}, "audio_base64"),
        ([1, 2], "オブジェクト"),
        ("calm piano", "オブジェクト"),
    ],
)
def test_bad_bodies_return_400_with_japanese_reason(client: TestClient, fake_engine: FakeEngine, body, fragment) -> None:
    response = post(client, "/api/v1/music", body)
    assert response.status_code == 400
    assert fragment in response.json()["error"]
    assert fake_engine.requests == []  # 検証に落ちたら生成しない


def test_invalid_json_returns_400(client: TestClient) -> None:
    response = client.post("/api/v1/music", content="{not json", headers=JSON_HEADERS)
    assert response.status_code == 400 and "JSON" in response.json()["error"]


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data"])
def test_non_json_content_type_returns_415(client: TestClient, fake_engine: FakeEngine, content_type: str) -> None:
    # 他サイトの form・fetch が事前確認なしで送れる型は断る（CSRF 対策）
    response = client.post("/api/v1/music", content=json.dumps({"prompt": "x"}), headers={"Content-Type": content_type})
    assert response.status_code == 415 and "application/json" in response.json()["error"]
    assert fake_engine.requests == []


def test_missing_content_type_returns_415(client: TestClient) -> None:
    assert client.post("/api/v1/music", content=b'{"prompt": "x"}').status_code == 415


def test_get_on_generate_endpoints_is_405_with_allow_header(client: TestClient) -> None:
    for path in ("/api/v1/music", "/api/v1/sound"):
        response = client.get(path)
        assert response.status_code == 405
        assert response.headers["allow"] == "POST" and "POST" in response.json()["error"]
    assert client.delete("/api/v1/music").status_code == 405


def test_unknown_path_and_auto_docs_are_not_exposed(client: TestClient) -> None:
    assert client.get("/api/v1/video").status_code == 404
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_returns_503_when_the_model_is_not_loaded(tmp_path: Path) -> None:
    engine = FakeEngine(tmp_path / "outputs", loaded=(params.KIND_MUSIC,))
    client = TestClient(rest.build_app(engine))
    assert post(client, "/api/v1/music", {"prompt": "x", "duration": 1}).status_code == 200
    response = post(client, "/api/v1/sound", {"prompt": "x", "duration": 1})
    assert response.status_code == 503 and "AudioGen" in response.json()["error"]


def test_engine_failure_returns_500_and_releases_the_gate(fake_engine: FakeEngine) -> None:
    gate = rest.PendingGate(1)
    client = TestClient(rest.build_app(fake_engine, gate), raise_server_exceptions=False)
    fake_engine.error = RuntimeError("CUDA out of memory")
    response = post(client, "/api/v1/music", {"prompt": "x", "duration": 1})
    assert response.status_code == 500
    assert "RuntimeError" in response.json()["error"] and "CUDA out of memory" in response.json()["error"]
    assert gate.pending == 0
    fake_engine.error = None
    assert post(client, "/api/v1/music", {"prompt": "x", "duration": 1}).status_code == 200


def test_full_gate_returns_503_with_retry_after_then_recovers(fake_engine: FakeEngine) -> None:
    gate = rest.PendingGate(1)
    app = rest.build_app(fake_engine, gate)
    fake_engine.block = True
    first_response: dict = {}

    def first_request() -> None:
        first_response["response"] = post(TestClient(app), "/api/v1/music", {"prompt": "x", "duration": 1})

    thread = threading.Thread(target=first_request)
    thread.start()
    try:
        assert fake_engine.entered.wait(timeout=10)
        assert gate.pending == 1
        second = post(TestClient(app), "/api/v1/sound", {"prompt": "y", "duration": 1})
        assert second.status_code == 503
        assert second.headers["retry-after"] == str(config.REST_RETRY_AFTER_SEC)
        assert "混み合って" in second.json()["error"]
        assert len(fake_engine.requests) == 1  # 2 件目は生成に進まない
    finally:
        fake_engine.release.set()
        thread.join(timeout=10)
    assert first_response["response"].status_code == 200
    assert gate.pending == 0
    fake_engine.block = False
    assert post(TestClient(app), "/api/v1/sound", {"prompt": "y", "duration": 1}).status_code == 200


# --------------------------------------------------------------------------
# PendingGate
# --------------------------------------------------------------------------


def test_pending_gate_counts_and_never_goes_negative() -> None:
    gate = rest.PendingGate(2)
    assert gate.try_enter() and gate.try_enter() and not gate.try_enter()
    assert gate.pending == 2
    gate.leave()
    assert gate.pending == 1 and gate.try_enter()
    gate.leave()
    gate.leave()
    gate.leave()  # 余分に呼んでも負にならない
    assert gate.pending == 0


# --------------------------------------------------------------------------
# install_routes: 画面（Gradio）のように、見つからない経路を自前で処理するアプリへ足す
# --------------------------------------------------------------------------


def _app_with_catch_all():
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse

    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def catch_all(path: str):
        return PlainTextResponse(f"画面側が処理した: /{path}", status_code=404)

    return app


def test_install_routes_are_checked_before_a_catch_all_route(fake_engine: FakeEngine) -> None:
    app = _app_with_catch_all()
    gate = rest.install_routes(app, fake_engine)
    assert gate.limit == config.REST_MAX_PENDING
    client = TestClient(app)
    # REST の経路が先に判定される（後ろに足すと、catch-all に取られて 404 になる）
    response = post(client, "/api/v1/music", {"prompt": "calm piano", "duration": 1})
    assert response.status_code == 200 and response.content[:4] == b"RIFF"
    assert client.get("/api/v1/health").json()["status"] == "ok"
    assert client.get("/api/v1/sound").status_code == 405
    # それ以外の経路は、これまでどおり catch-all が処理する
    other = client.get("/some/other/path")
    assert other.status_code == 404 and "画面側が処理した" in other.text


def test_install_routes_keeps_existing_routes_in_front_order(fake_engine: FakeEngine) -> None:
    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/existing")
    def existing() -> dict:
        return {"existing": True}

    rest.install_routes(app, fake_engine, rest.PendingGate(3))
    client = TestClient(app)
    assert client.get("/existing").json() == {"existing": True}
    assert client.get("/api/v1/health").json()["max_pending"] == 3


# --------------------------------------------------------------------------
# 元の音（mode・audio_base64）
# --------------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


@pytest.mark.parametrize("kind, mode", [("music", "continuation"), ("music", "melody"), ("sound", "continuation")])
def test_source_audio_is_passed_to_the_engine_and_mode_is_in_headers(client: TestClient, fake_engine: FakeEngine, kind, mode) -> None:
    body = {"prompt": "x", "duration": 5, "mode": mode, "audio_base64": _b64(b"RIFF-fake-audio")}
    response = post(client, f"/api/v1/{kind}", body)
    assert response.status_code == 200, response.text
    request = fake_engine.requests[0]
    assert request.mode == mode and request.source is not None and request.source.data == b"RIFF-fake-audio"
    assert response.headers["X-Acraft-Mode"] == mode
    assert f"-{mode}-" in response.headers["X-Acraft-File"]


def test_melody_on_sound_is_rejected(client: TestClient, fake_engine: FakeEngine) -> None:
    response = post(client, "/api/v1/sound", {"prompt": "x", "mode": "melody", "audio_base64": _b64(b"RIFF")})
    assert response.status_code == 400 and "モード" in response.json()["error"]
    assert fake_engine.requests == []


def test_unreadable_source_audio_from_the_engine_is_400(client: TestClient) -> None:
    response = post(client, "/api/v1/music", {"prompt": "x", "mode": "continuation", "audio_base64": _b64(b"BAD audio")})
    assert response.status_code == 400 and "読めません" in response.json()["error"]


def test_keep_false_also_removes_the_source_copy(client: TestClient, fake_engine: FakeEngine) -> None:
    body = {"prompt": "x", "duration": 5, "mode": "continuation", "audio_base64": _b64(b"RIFF"), "keep": False}
    assert post(client, "/api/v1/music", body).status_code == 200
    assert list(fake_engine.outputs_dir.iterdir()) == []


def test_melody_mode_is_503_when_the_melody_model_is_not_loaded(tmp_path: Path) -> None:
    engine = FakeEngine(tmp_path / "outputs", melody=False)
    client = TestClient(rest.build_app(engine))
    response = post(client, "/api/v1/music", {"prompt": "x", "mode": "melody", "audio_base64": _b64(b"RIFF")})
    assert response.status_code == 503 and "melody" in response.json()["error"]
    assert client.get("/api/v1/health").json()["kinds"]["music"]["modes"] == ["text", "continuation"]
