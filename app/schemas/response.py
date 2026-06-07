"""
Единый контракт API-ответов.

Улучшенная версия предложенной схемы:
* Generic ServerResponse[T] со строгой типизацией.
* SuccessResponse[T] / ErrorResponse — всегда консистентны.
* ResponseMeta — опциональные метаданные (request_id, пагинация, latency).
* ErrorCode — расширяемый enum машинных кодов ошибок.
* Сериализация моделей — через Pydantic v2 (pydantic-core, Rust) по response_model.

Любой ответ клиенту имеет вид:
    {"status": true,  "data": {...}, "meta": {...}}
    {"status": false, "data": {"code": "...", "message": "..."}, "meta": {...}}
"""

from __future__ import annotations

from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel

from app.core.context import get_request_id

T = TypeVar("T")


class ErrorCode(StrEnum):
    """Машинные коды ошибок. Расширяйте по мере роста доменов."""

    INTERNAL = "internal_error"
    VALIDATION = "validation_error"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    RATE_LIMITED = "rate_limited"
    BAD_REQUEST = "bad_request"
    UNAVAILABLE = "service_unavailable"


class ResponseMeta(BaseModel):
    """Опциональные метаданные ответа. Все поля необязательны."""

    request_id: str | None = None
    # Для постраничных коллекций
    page: int | None = None
    per_page: int | None = None
    total: int | None = None  # всего записей
    pages: int | None = None  # всего страниц (ceil(total / per_page))
    # Любые доменные расширения (например latency_ms)
    extra: dict[str, object] | None = None


class ServerResponse(BaseModel, Generic[T]):
    """Базовый конверт ответа."""

    status: bool
    data: T
    meta: ResponseMeta | None = None


class SuccessResponse(ServerResponse[T]):
    status: bool = True


class EmptyResponse(BaseModel):
    """Используется, когда тело данных не нужно (204-подобные операции)."""

    status: bool = True
    meta: ResponseMeta | None = None


class ErrorData(BaseModel):
    """Тело ошибки внутри конверта."""

    code: ErrorCode = ErrorCode.INTERNAL
    message: str
    # Детализация полей валидации и т.п.
    details: list[dict[str, object]] | None = None


class ErrorResponse(ServerResponse[ErrorData]):
    status: bool = False


def success(
    data: T,
    *,
    meta: ResponseMeta | None = None,
) -> SuccessResponse[T]:
    """
    Хелпер для сборки успешного ответа.
    request_id подмешивается из контекста запроса автоматически (сквозная трассировка),
    если вызов идёт в рамках HTTP-запроса и не задан явно.
    """
    rid = get_request_id()
    if meta is None:
        meta = ResponseMeta(request_id=rid) if rid else None
    elif meta.request_id is None and rid:
        meta.request_id = rid
    return SuccessResponse[T](data=data, meta=meta)


def empty() -> EmptyResponse:
    """Пустой успешный ответ с request_id из контекста (для delete/logout и т.п.)."""
    rid = get_request_id()
    return EmptyResponse(meta=ResponseMeta(request_id=rid) if rid else None)


def error(
    message: str,
    *,
    code: ErrorCode = ErrorCode.INTERNAL,
    details: list[dict[str, object]] | None = None,
    request_id: str | None = None,
) -> ErrorResponse:
    """Хелпер для сборки ответа-ошибки."""
    meta = ResponseMeta(request_id=request_id) if request_id else None
    return ErrorResponse(data=ErrorData(code=code, message=message, details=details), meta=meta)
