"""Тесты доступа к документации (/docs, /openapi.json) с опциональным Basic Auth."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Environment, settings

pytestmark = pytest.mark.asyncio


def _set(monkeypatch, *, env: Environment, user: str, password: str, enabled: bool = True) -> None:
    monkeypatch.setattr(settings, "environment", env)
    monkeypatch.setattr(settings, "docs_enabled", enabled)
    monkeypatch.setattr(settings, "docs_basic_auth_user", user)
    monkeypatch.setattr(settings, "docs_basic_auth_password", password)


def _client() -> AsyncClient:
    from app.main import create_app

    app = create_app()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_docs_open_in_dev_without_creds(monkeypatch):
    _set(monkeypatch, env=Environment.DEV, user="", password="")
    async with _client() as c:
        assert (await c.get("/docs")).status_code == 200
        assert (await c.get("/openapi.json")).status_code == 200


async def test_docs_basic_auth_when_creds_set(monkeypatch):
    _set(monkeypatch, env=Environment.DEV, user="admin", password="secret")
    async with _client() as c:
        r = await c.get("/docs")
        assert r.status_code == 401
        assert r.headers.get("www-authenticate", "").lower().startswith("basic")  # браузер спросит
        assert (await c.get("/docs", auth=("admin", "secret"))).status_code == 200
        assert (await c.get("/docs", auth=("admin", "wrong"))).status_code == 401
        # openapi.json защищён так же
        assert (await c.get("/openapi.json")).status_code == 401
        assert (await c.get("/openapi.json", auth=("admin", "secret"))).status_code == 200


async def test_docs_hidden_in_prod_without_creds(monkeypatch):
    _set(monkeypatch, env=Environment.PROD, user="", password="")
    async with _client() as c:
        assert (await c.get("/docs")).status_code == 404
        assert (await c.get("/openapi.json")).status_code == 404
        assert (await c.get("/redoc")).status_code == 404


async def test_docs_in_prod_with_basic_auth(monkeypatch):
    _set(monkeypatch, env=Environment.PROD, user="admin", password="secret")
    async with _client() as c:
        assert (await c.get("/docs")).status_code == 401
        assert (await c.get("/docs", auth=("admin", "secret"))).status_code == 200


async def test_docs_fully_disabled(monkeypatch):
    _set(monkeypatch, env=Environment.DEV, user="", password="", enabled=False)
    async with _client() as c:
        assert (await c.get("/docs")).status_code == 404
        assert (await c.get("/openapi.json")).status_code == 404
