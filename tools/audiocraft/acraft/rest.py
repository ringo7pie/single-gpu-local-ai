"""REST の口。文章を JSON で POST すると、生成した wav をそのまま返す。

    POST /api/v1/music   作曲（MusicGen）。本文: {"prompt": "...", "duration": 10, "seed": -1, ...}
    POST /api/v1/sound   効果音（AudioGen）。本文は同じ
    GET  /api/v1/health  状態と、種類ごとのモデル・サンプルレート・長さの範囲

Gradio の画面（同じポート 7862 の /）と同じ Engine を使うので、生成は画面の分も含めて 1 度に 1 件。
認証は無い（LAN 内の前提。入口ページと同じ）。ブラウザの別サイトから勝手に叩かれないよう、
Content-Type が application/json でない POST は 415 で断る（JSON は cross-origin では事前確認が要り、
こちらは事前確認に応答しないため）。

このモジュールは fastapi を使うが、gradio・torch・audiocraft は import しない
（試験で偽の Engine を差し込める）。画面との合体は acraft.ui が行い、Gradio が作った FastAPI アプリへ
install_routes で REST の経路を足す。
"""

from __future__ import annotations

import sys
import threading
import traceback
from typing import Any, Protocol

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from . import config, params

API_PREFIX = "/api/v1"


class EngineLike(Protocol):
    """rest.py が Engine に求める部分（試験では偽物を使う）。"""

    model_ids: dict[str, str]
    device: str

    def loaded_kinds(self) -> tuple[str, ...]: ...

    def generate(self, request: params.GenerationParams, out_path: Any = None, now: Any = None) -> Any: ...


class PendingGate:
    """同時に受け付ける依頼の数を絞る（順番待ちを含む）。"""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._count = 0
        self._lock = threading.Lock()

    @property
    def pending(self) -> int:
        with self._lock:
            return self._count

    def try_enter(self) -> bool:
        with self._lock:
            if self._count >= self.limit:
                return False
            self._count += 1
            return True

    def leave(self) -> None:
        with self._lock:
            self._count = max(0, self._count - 1)


def _error(status_code: int, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code, headers=headers)


def _generate_blocking(eng: EngineLike, rest_request: params.RestRequest) -> tuple[bytes, dict[str, Any]]:
    """生成して wav の中身を読む（スレッドプールで動かす）。keep が偽なら、サーバーに残したファイルを消す。"""

    result = eng.generate(rest_request.params)
    wav_bytes = result.wav_path.read_bytes()
    if not rest_request.keep:
        for path in (result.wav_path, result.meta_path):
            path.unlink(missing_ok=True)
    return wav_bytes, result.metadata


def build_router(eng: EngineLike, gate: PendingGate) -> APIRouter:
    router = APIRouter(prefix=API_PREFIX)

    def make_handler(kind: str):
        async def handler(request: Request) -> Response:
            content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type != "application/json":
                return _error(415, "Content-Type は application/json にしてください。")
            try:
                body = await request.json()
            except ValueError:
                return _error(400, "本文が JSON として読めません。")
            try:
                rest_request = params.parse_rest_body(kind, body)
            except params.ParamError as error:
                return _error(400, str(error))
            if kind not in eng.loaded_kinds():
                return _error(503, f"{params.KIND_SPECS[kind].model_label} のモデルが読み込まれていません。")
            if not gate.try_enter():
                return _error(
                    503,
                    f"依頼が混み合っています（同時に受け付けるのは {gate.limit} 件まで）。しばらくしてからやり直してください。",
                    headers={"Retry-After": str(config.REST_RETRY_AFTER_SEC)},
                )
            try:
                wav_bytes, metadata = await run_in_threadpool(_generate_blocking, eng, rest_request)
            except Exception as error:  # noqa: BLE001 - 原因を応答と標準エラーに出す
                traceback.print_exc(file=sys.stderr)
                return _error(500, f"生成に失敗しました: {type(error).__name__}: {error}")
            finally:
                gate.leave()
            return Response(
                content=wav_bytes,
                media_type="audio/wav",
                headers=params.build_response_headers(metadata, rest_request.keep),
            )

        return handler

    async def method_not_allowed() -> Response:
        return _error(405, "POST で呼んでください（本文は JSON）。使い方は画面の「API の使い方」にあります。", headers={"Allow": "POST"})

    for kind in params.KINDS:
        router.add_api_route(f"/{kind}", make_handler(kind), methods=["POST"])
        # 画面（Gradio）と同じアプリに載せるとき、GET などが画面側の 404 に落ちないよう、405 を明示する。
        router.add_api_route(f"/{kind}", method_not_allowed, methods=["GET", "PUT", "PATCH", "DELETE"])

    @router.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "device": eng.device,
            "loaded": list(eng.loaded_kinds()),
            "pending": gate.pending,
            "max_pending": gate.limit,
            "kinds": {
                kind: {
                    "model": eng.model_ids[kind],
                    "sample_rate": spec.sample_rate,
                    "duration_sec": {"min": spec.min_duration, "max": spec.max_duration, "default": spec.default_duration},
                }
                for kind, spec in params.KIND_SPECS.items()
            },
        }

    return router


def install_routes(app: FastAPI, eng: EngineLike, gate: PendingGate | None = None) -> PendingGate:
    """既にあるアプリ（Gradio が作ったもの）へ REST の経路を足す。足した経路は、既存の経路より先に判定されるよう先頭へ置く。

    Gradio のアプリは、画面を "/" に置き、見つからない経路を自前で処理する。後ろに足すと REST の経路に届かないことがある。
    """

    used_gate = gate if gate is not None else PendingGate(config.REST_MAX_PENDING)
    before = len(app.router.routes)
    app.include_router(build_router(eng, used_gate))
    added = app.router.routes[before:]
    del app.router.routes[before:]
    app.router.routes[0:0] = added
    return used_gate


def build_app(eng: EngineLike, gate: PendingGate | None = None) -> FastAPI:
    """REST の口だけを持つ FastAPI アプリ（試験用。本番は Gradio のアプリへ install_routes で足す）。

    自動の API 文書（/docs・/openapi.json）は出さない。本文を自前で検証しており、文書が実態とずれるため。
    使い方は tools/audiocraft/README.md と画面の「API の使い方」にある。
    """

    app = FastAPI(title="acraft REST", docs_url=None, redoc_url=None, openapi_url=None)
    install_routes(app, eng, gate)
    return app
