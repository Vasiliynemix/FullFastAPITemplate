"""
Глобальные обработчики исключений FastAPI.

Гарантируют: КЛИЕНТ НИКОГДА не видит сырую ошибку FastAPI/Starlette/Pydantic.
Всё конвертируется в единый ErrorResponse. Регистрируется в main.create_app().
"""

from __future__ import annotations

import traceback

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import caller_location, get_logger
from app.exceptions.base import ServerException
from app.schemas.response import ErrorCode, error

logger = get_logger("exceptions")


def _raised_at(exc: BaseException) -> str | None:
    """Место, где исключение было фактически брошено (последний кадр traceback)."""
    tb = exc.__traceback__
    if tb is None:
        return None
    frames = traceback.extract_tb(tb)
    if not frames:
        return None
    last = frames[-1]  # самый внутренний кадр = точка raise
    return caller_location(last.filename, last.lineno)


def _render(
    status_code: int,
    message: str,
    code: ErrorCode,
    details: list[dict[str, object]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    payload = error(message, code=code, details=details)
    # model_dump(mode="json") даёт JSON-safe примитивы — стандартный JSONResponse их кодирует
    return JSONResponse(
        status_code=status_code, content=payload.model_dump(mode="json"), headers=headers
    )


async def _server_exception_handler(_: Request, exc: ServerException) -> JSONResponse:
    # Контролируемая ошибка — это ожидаемый путь, логируем как warning.
    # raised_at — где именно бросили исключение (напр. app/api/deps.py:98).
    logger.warning(
        "server_exception",
        code=exc.code.value,
        status=exc.status_code,
        msg=exc.message,
        raised_at=_raised_at(exc),
    )
    return _render(exc.status_code, exc.message, exc.code, exc.details)


async def _validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Оборачиваем ошибки валидации Pydantic в единый контракт
    details = [
        {
            "loc": list(err.get("loc", [])),
            "msg": err.get("msg", ""),
            "type": err.get("type", ""),
        }
        for err in exc.errors()
    ]
    logger.info("validation_error", count=len(details))
    return _render(422, "Validation failed", ErrorCode.VALIDATION, details)


async def _http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    # 404/405 и прочее от Starlette — тоже в единый контракт
    code_map = {
        400: ErrorCode.BAD_REQUEST,
        401: ErrorCode.UNAUTHORIZED,
        403: ErrorCode.FORBIDDEN,
        404: ErrorCode.NOT_FOUND,
        429: ErrorCode.RATE_LIMITED,
        503: ErrorCode.UNAVAILABLE,
    }
    code = code_map.get(exc.status_code, ErrorCode.INTERNAL)
    # Сохраняем заголовки HTTPException (например WWW-Authenticate для Basic Auth на /docs,
    # Retry-After и т.п.) — иначе браузер не покажет окно ввода логина/пароля.
    return _render(exc.status_code, str(exc.detail), code, headers=getattr(exc, "headers", None))


async def _unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
    # Непредвиденная ошибка — полный traceback в лог, наружу только generic-сообщение.
    # Сюда НЕ попадают контролируемые ServerException (у них свой хендлер) — значит в
    # Sentry уходят только настоящие 500-е, без шума от ожидаемых бизнес-ошибок.
    logger.error("unhandled_exception", raised_at=_raised_at(exc), exc_info=exc)
    # capture_exception — no-op, если Sentry не инициализирован (нет DSN)
    import sentry_sdk

    sentry_sdk.capture_exception(exc)
    return _render(500, "Internal server error", ErrorCode.INTERNAL)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ServerException, _server_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _unhandled_handler)
