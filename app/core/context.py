"""
Контекст запроса через contextvars.

contextvars безопасны для async: каждый запрос видит свой request_id/trace_id
без передачи их явно через все слои. Используется structlog'ом и middleware.
"""

from __future__ import annotations

from contextvars import ContextVar

# request_id живёт в рамках одного HTTP-запроса
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
trace_id_ctx: ContextVar[str | None] = ContextVar("trace_id", default=None)


def get_request_id() -> str | None:
    return request_id_ctx.get()


def set_request_id(value: str) -> None:
    request_id_ctx.set(value)


def get_trace_id() -> str | None:
    return trace_id_ctx.get()


def set_trace_id(value: str) -> None:
    trace_id_ctx.set(value)
