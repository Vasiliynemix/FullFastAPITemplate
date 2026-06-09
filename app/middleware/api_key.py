"""
Глобальный gate по одному API-ключу.

Когда GLOBAL_API_KEY_ENABLED=true — ВЕСЬ API (кроме health/docs/openapi) требует
заголовок `X-API-Key`, равный GLOBAL_API_KEY. Это режим «сервис закрыт одним
ключом»: им пользуются только доверенные продукты (service-to-service).

Работает независимо от JWT:
* только gate            — закрыт весь сервис одним ключом;
* gate + JWT             — внешний контур по ключу + ролевая авторизация внутри;
* только JWT (gate off)  — публичный API с защитой отдельных ручек.

Сравнение ключа — в постоянном времени (hmac.compare_digest), чтобы не утекало
время сравнения. При отсутствии/несовпадении — единый ErrorResponse 401.
"""

from __future__ import annotations

import hmac

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings
from app.schemas.response import ErrorCode, error

# Те же исключения, что и у rate limit — служебные пути не закрываем ключом
_EXEMPT_PREFIXES = (f"{settings.api_v1_prefix}/health", "/docs", "/redoc", "/openapi.json")
_API_KEY_HEADER = b"x-api-key"


class ApiKeyMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not settings.global_api_key_enabled:
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if path.startswith(_EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        provided = headers.get(_API_KEY_HEADER, b"").decode()

        if not self._is_valid(provided):
            await self._reject(scope, receive, send)
            return

        await self.app(scope, receive, send)

    @staticmethod
    def _is_valid(provided: str) -> bool:
        expected = settings.global_api_key
        # Пустой ожидаемый ключ при включённом gate = мисконфигурация -> закрываем доступ
        if not expected:
            return False
        return hmac.compare_digest(provided, expected)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        payload = error("Invalid or missing API key", code=ErrorCode.UNAUTHORIZED)
        response = JSONResponse(status_code=401, content=payload.model_dump(mode="json"))
        await response(scope, receive, send)
