"""Интеграционные тесты роутера /files поверх in-memory fake_storage (без S3)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings

pytestmark = pytest.mark.asyncio


def _app(fake_storage, monkeypatch):
    from app.api.deps import get_storage
    from app.main import create_app

    # Включаем storage (иначе /files не регистрируется), без Redis (rate limit) и без
    # глобального gate (иначе /files требует X-API-Key) — изолируем от значений .env.
    monkeypatch.setattr(settings, "storage_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    monkeypatch.setattr(settings, "global_api_key_enabled", False)
    app = create_app()
    app.dependency_overrides[get_storage] = lambda: fake_storage
    return app


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_upload_download_delete_roundtrip(fake_storage, monkeypatch):
    async with _client(_app(fake_storage, monkeypatch)) as c:
        # upload
        r = await c.post(
            "/api/v1/files",
            files={"file": ("hello.txt", b"hello world", "text/plain")},
        )
        assert r.status_code == 201
        body = r.json()["data"]
        key, url = body["key"], body["url"]
        assert body["size"] == 11
        assert key.startswith("uploads/") and key.endswith(".txt")
        assert url == f"/api/v1/files/download/{key}"

        # download — содержимое то же
        r = await c.get(url)
        assert r.status_code == 200
        assert r.content == b"hello world"

        # presigned URL отдельной ручкой
        r = await c.get(f"/api/v1/files/url/{key}")
        assert r.status_code == 200
        assert r.json()["data"]["url"] == url

        # delete -> потом 404 на скачивании
        r = await c.delete(f"/api/v1/files/{key}")
        assert r.status_code == 200
        assert (await c.get(url)).status_code == 404


async def test_download_missing_returns_404(fake_storage, monkeypatch):
    async with _client(_app(fake_storage, monkeypatch)) as c:
        r = await c.get("/api/v1/files/download/uploads/nope.bin")
        assert r.status_code == 404


async def test_files_router_absent_when_storage_disabled(monkeypatch):
    # storage_enabled=false => роутер /files вообще не регистрируется
    from app.main import create_app

    monkeypatch.setattr(settings, "storage_enabled", False)
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    monkeypatch.setattr(settings, "global_api_key_enabled", False)
    async with _client(create_app()) as c:
        r = await c.post("/api/v1/files", files={"file": ("a.txt", b"x", "text/plain")})
        assert r.status_code == 404
