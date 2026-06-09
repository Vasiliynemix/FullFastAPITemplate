"""
Middleware глобального rate limiting (Redis).

Идентификатор клиента (per-key или per-IP):
* если включён GLOBAL_API_KEY_ENABLED — лимитируем per-key по X-API-Key (ключ уже
  провалидирован ApiKeyMiddleware выше по стеку), т.е. у каждого продукта своя квота;
* иначе — per-IP (за Nginx берём первый адрес X-Forwarded-For).

Per-user (по id из JWT) намеренно НЕ делаем здесь: ASGI-middleware ещё не знает
авторизованного пользователя, а доверять непроверенному `sub` из токена нельзя (обход).
Для пер-юзер лимита вешайте отдельную зависимость ПОСЛЕ аутентификации.

На каждый запрос — одна атомарная проверка в Redis. При превышении — единый
ErrorResponse 429 с Retry-After / X-RateLimit-*. Отключается RATE_LIMIT_ENABLED.
Health-эндпоинты не лимитируются.
"""

from __future__ import annotations

import hashlib

from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings
from app.ratelimit.limiter import get_rate_limiter
from app.schemas.response import ErrorCode, error

# Те же исключения, что и у ApiKeyMiddleware. ВАЖНО: health под префиксом версии,
# поэтому берём полный путь из настроек, иначе startswith не сработает.
_EXEMPT_PREFIXES = (f"{settings.api_v1_prefix}/health", "/docs", "/openapi.json", "/redoc")


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not settings.rate_limit_enabled:
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if path.startswith(_EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        identity = self._identity(request)
        result = await get_rate_limiter().check(identity)

        if not result.allowed:
            payload = error("Too many requests", code=ErrorCode.RATE_LIMITED)
            response = JSONResponse(
                status_code=429,
                content=payload.model_dump(mode="json"),
                headers={
                    "Retry-After": str(result.retry_after),
                    "X-RateLimit-Limit": str(result.limit),
                    "X-RateLimit-Remaining": str(result.remaining),
                },
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)

    @classmethod
    def _identity(cls, request: Request) -> str:
        # per-key: ключ уже провалидирован ApiKeyMiddleware (выше по стеку) — надёжный id.
        # Храним не сам секрет, а его хэш (Redis-ключ не должен содержать секрет).
        if settings.global_api_key_enabled:
            api_key = request.headers.get("x-api-key")
            if api_key:
                digest = hashlib.sha256(api_key.encode()).hexdigest()[:16]
                return f"key:{digest}"
        return f"ip:{cls._client_ip(request)}"

    @staticmethod
    def _client_ip(request: Request) -> str:
        # За Nginx реальный IP в X-Forwarded-For (первый адрес)
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
        return request.client.host if request.client else "unknown"
