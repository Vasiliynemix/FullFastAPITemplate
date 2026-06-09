"""Контракт Idempotency-Key на уровне ручек.

* На денежных ручках (deposit/withdraw/создание счёта) ключ ОБЯЗАТЕЛЕН — без заголовка 422.
* На create_user — опционален (но если прислан, валидируется 8..255).

Сервисы подменены заглушкой: до них дело не доходит (422 раньше), зато тест не зависит от БД.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings

_ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def _app(monkeypatch):
    from app.api.deps import get_account_service, get_user_service
    from app.main import create_app

    # без rate limit (Redis) и без глобального gate (X-API-Key) — изолируемся от .env
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    monkeypatch.setattr(settings, "global_api_key_enabled", False)
    app = create_app()
    app.dependency_overrides[get_user_service] = lambda: object()
    app.dependency_overrides[get_account_service] = lambda: object()
    return app


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _key_param(schema, path):
    params = schema["paths"][path]["post"].get("parameters", [])
    return next((p for p in params if p["in"] == "header" and p["name"] == "Idempotency-Key"), None)


# Денежные/создающие счёт ручки, где ключ обязателен.
_REQUIRED = [
    ("/api/v1/accounts", {"user_id": _ZERO_UUID, "name": "Main"}),
    (f"/api/v1/accounts/{_ZERO_UUID}/deposit", {"amount": "10.00"}),
    (f"/api/v1/accounts/{_ZERO_UUID}/withdraw", {"amount": "10.00"}),
]
_REQUIRED_OPENAPI_PATHS = [
    "/api/v1/accounts",
    "/api/v1/accounts/{account_id}/deposit",
    "/api/v1/accounts/{account_id}/withdraw",
]


@pytest.mark.parametrize("path, body", _REQUIRED)
async def test_missing_idempotency_key_is_422(monkeypatch, path, body):
    async with _client(_app(monkeypatch)) as c:
        r = await c.post(path, json=body)

    assert r.status_code == 422
    # ошибка указывает именно на заголовок Idempotency-Key
    assert "idempotency" in r.text.lower()


@pytest.mark.parametrize("path", _REQUIRED_OPENAPI_PATHS)
def test_openapi_marks_idempotency_key_required(monkeypatch, path):
    """В схеме OpenAPI заголовок помечен required=true (без Redis/БД)."""
    key_param = _key_param(_app(monkeypatch).openapi(), path)
    assert key_param is not None, f"{path}: нет header-параметра Idempotency-Key"
    assert key_param["required"] is True


def test_openapi_marks_create_user_idempotency_optional(monkeypatch):
    """create_user принимает Idempotency-Key, но НЕ требует (required=false)."""
    key_param = _key_param(_app(monkeypatch).openapi(), "/api/v1/users")
    assert key_param is not None, "нет header-параметра Idempotency-Key у POST /users"
    assert key_param["required"] is False
