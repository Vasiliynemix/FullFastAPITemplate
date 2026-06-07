"""
Middleware request context + структурный лог запроса.

Делает (минимально и быстро — это hot path):
* присваивает/прокидывает request_id (заголовок X-Request-ID) и trace_id;
* кладёт их в contextvars (видны во всех логах запроса);
* меряет латентность монотонными часами;
* пишет ОДНУ структурную запись на запрос: метод, путь, статус, latency_ms.

Реализовано как «чистый ASGI» middleware (без BaseHTTPMiddleware) — меньше
оверхед и без дополнительной обёртки над response в горячем пути.
"""

from __future__ import annotations

import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.context import request_id_ctx, trace_id_ctx
from app.core.logging import get_logger

logger = get_logger("http")

_REQUEST_ID_HEADER = b"x-request-id"
_TRACE_ID_HEADER = b"x-trace-id"


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        request_id = headers.get(_REQUEST_ID_HEADER, b"").decode() or uuid.uuid4().hex
        trace_id = headers.get(_TRACE_ID_HEADER, b"").decode() or request_id

        rid_token = request_id_ctx.set(request_id)
        tid_token = trace_id_ctx.set(trace_id)
        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                # Возвращаем request_id клиенту
                headers_list = message.setdefault("headers", [])
                headers_list.append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = (time.perf_counter() - start) * 1000
            logger.info(
                "request",
                method=scope.get("method"),
                endpoint=scope.get("path"),
                status_code=status_code,
                latency_ms=round(elapsed, 2),
            )
            request_id_ctx.reset(rid_token)
            trace_id_ctx.reset(tid_token)
