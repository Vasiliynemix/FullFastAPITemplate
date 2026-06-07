"""
Авторизационные миксины и готовые «обёртки» поверх BaseHTTPClient.

Зачем миксины: авторизация ортогональна транспорту и бывает разной — поэтому она
вынесена в подмешиваемые классы. Миксины кооперативны (вызывают super()), их можно
комбинировать. Для удобства тут же собраны готовые классы (миксин + база): наследуйте
их напрямую вместо ручной сборки.

Конкретный клиент:
    class PartnerAPI(BearerHTTPClient):
        base_url = "https://partner.example/api"
        service_name = "partner"
    api = PartnerAPI(token="...")
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.clients.base import BaseHTTPClient, ExternalAPIError


class BearerAuthMixin:
    """Статический Bearer-токен в заголовке Authorization."""

    def __init__(self, *args: Any, token: str, **kw: Any) -> None:
        self._token = token
        super().__init__(*args, **kw)

    async def prepare(self, method: str, url: str, options: dict[str, Any]) -> None:
        await super().prepare(method, url, options)  # type: ignore[misc]
        options["headers"]["Authorization"] = f"Bearer {self._token}"


class ApiKeyHeaderMixin:
    """Ключ в произвольном ЗАГОЛОВКЕ (имя задаётся api_key_header)."""

    api_key_header: str = "X-Api-Key"

    def __init__(
        self, *args: Any, api_key: str, api_key_header: str | None = None, **kw: Any
    ) -> None:
        self._api_key = api_key
        if api_key_header is not None:
            self.api_key_header = api_key_header
        super().__init__(*args, **kw)

    def default_headers(self) -> dict[str, str]:
        return {**super().default_headers(), self.api_key_header: self._api_key}  # type: ignore[misc]


class ApiKeyQueryMixin:
    """Ключ в QUERY-параметре (имя задаётся api_key_param)."""

    api_key_param: str = "api_key"

    def __init__(
        self, *args: Any, api_key: str, api_key_param: str | None = None, **kw: Any
    ) -> None:
        self._api_key = api_key
        if api_key_param is not None:
            self.api_key_param = api_key_param
        super().__init__(*args, **kw)

    async def prepare(self, method: str, url: str, options: dict[str, Any]) -> None:
        await super().prepare(method, url, options)  # type: ignore[misc]
        options.setdefault("params", {})[self.api_key_param] = self._api_key


class BasicAuthMixin:
    """HTTP Basic (login/pass) — через httpx.BasicAuth на уровне клиента."""

    def __init__(self, *args: Any, username: str, password: str, **kw: Any) -> None:
        kw.setdefault("auth", httpx.BasicAuth(username, password))
        super().__init__(*args, **kw)


class TokenLoginMixin:
    """
    Авторизация по login/pass с обменом на токен.

    Поток: лениво логинимся (POST login_path) → получаем токен → шлём его как Bearer.
    На 401 — сбрасываем токен и повторяем запрос один раз (перелогин). Логин защищён
    Lock'ом от «стада» параллельных запросов.

    Переопределите `_login()` под формат внешнего API (по умолчанию ждём access_token).
    """

    login_path: str = "/auth/login"

    def __init__(self, *args: Any, username: str, password: str, **kw: Any) -> None:
        self._username = username
        self._password = password
        self._token: str | None = None
        self._login_lock = asyncio.Lock()
        super().__init__(*args, **kw)

    async def _login(self) -> str:
        data = await self.request(  # type: ignore[attr-defined]
            "POST",
            self.login_path,
            json={"username": self._username, "password": self._password},
        )
        return data["access_token"]

    async def _ensure_token(self) -> None:
        if self._token is None:
            async with self._login_lock:
                if self._token is None:  # double-check под локом
                    self._token = await self._login()

    async def prepare(self, method: str, url: str, options: dict[str, Any]) -> None:
        await super().prepare(method, url, options)  # type: ignore[misc]
        if url == self.login_path:
            return  # сам логин — без токена (иначе рекурсия)
        await self._ensure_token()
        options["headers"]["Authorization"] = f"Bearer {self._token}"

    async def request(self, method: str, url: str, **kw: Any) -> Any:
        try:
            return await super().request(method, url, **kw)  # type: ignore[misc]
        except ExternalAPIError as exc:
            # Токен протух -> сбрасываем и повторяем один раз
            if exc.upstream_status == 401 and url != self.login_path and self._token:
                self._token = None
                return await super().request(method, url, **kw)  # type: ignore[misc]
            raise


# ----------------------------------------------------------------------
# Готовые обёртки (миксин + база) — наследуйте напрямую
# ----------------------------------------------------------------------
class BearerHTTPClient(BearerAuthMixin, BaseHTTPClient):
    pass


class ApiKeyHeaderHTTPClient(ApiKeyHeaderMixin, BaseHTTPClient):
    pass


class ApiKeyQueryHTTPClient(ApiKeyQueryMixin, BaseHTTPClient):
    pass


class BasicAuthHTTPClient(BasicAuthMixin, BaseHTTPClient):
    pass


class LoginTokenHTTPClient(TokenLoginMixin, BaseHTTPClient):
    pass
