"""
Контролируемые исключения.

ServerException — ЕДИНСТВЕННЫЙ тип исключений, который слой сервисов бросает
осознанно. Глобальный обработчик превращает его в ErrorResponse. Любое другое
(непредвиденное) исключение тоже перехватывается, но логируется как 500.

Подклассы — синтаксический сахар для частых HTTP-ситуаций.
"""

from __future__ import annotations

from app.schemas.response import ErrorCode


class ServerException(Exception):
    """Базовое контролируемое исключение домена/сервисов."""

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        code: ErrorCode = ErrorCode.INTERNAL,
        details: list[dict[str, object]] | None = None,
    ) -> None:
        self.status_code = status_code
        self.message = message
        self.code = code
        self.details = details
        super().__init__(message)


class BadRequestError(ServerException):
    def __init__(self, message: str = "Bad request", **kw: object) -> None:
        super().__init__(400, message, code=ErrorCode.BAD_REQUEST, **kw)  # type: ignore[arg-type]


class UnauthorizedError(ServerException):
    def __init__(self, message: str = "Unauthorized", **kw: object) -> None:
        super().__init__(401, message, code=ErrorCode.UNAUTHORIZED, **kw)  # type: ignore[arg-type]


class ForbiddenError(ServerException):
    def __init__(self, message: str = "Forbidden", **kw: object) -> None:
        super().__init__(403, message, code=ErrorCode.FORBIDDEN, **kw)  # type: ignore[arg-type]


class NotFoundError(ServerException):
    def __init__(self, message: str = "Not found", **kw: object) -> None:
        super().__init__(404, message, code=ErrorCode.NOT_FOUND, **kw)  # type: ignore[arg-type]


class ConflictError(ServerException):
    def __init__(self, message: str = "Conflict", **kw: object) -> None:
        super().__init__(409, message, code=ErrorCode.CONFLICT, **kw)  # type: ignore[arg-type]


class RateLimitedError(ServerException):
    def __init__(self, message: str = "Too many requests", **kw: object) -> None:
        super().__init__(429, message, code=ErrorCode.RATE_LIMITED, **kw)  # type: ignore[arg-type]
