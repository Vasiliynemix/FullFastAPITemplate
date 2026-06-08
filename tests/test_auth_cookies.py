"""Куки авторизации: хелпер set/clear + чтение access-токена из куки в get_current_user."""

from __future__ import annotations

import pytest
from fastapi import Response
from starlette.requests import Request

from app.api.deps import get_current_user
from app.core.config import AuthTransport, Settings, settings
from app.exceptions.base import UnauthorizedError
from app.schemas.auth import TokenPair
from app.security.cookies import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    clear_auth_cookies,
    set_auth_cookies,
)
from app.security.jwt import create_access_token
from app.security.roles import Role
from app.security.session_store import SessionStore


def _set_cookie_headers(response: Response) -> list[str]:
    return [v.decode() for k, v in response.raw_headers if k == b"set-cookie"]


def test_set_auth_cookies_httponly_and_paths():
    resp = Response()
    set_auth_cookies(resp, TokenPair(access_token="acc", refresh_token="ref", expires_in=900))
    cookies = _set_cookie_headers(resp)

    access = next(c for c in cookies if c.startswith(f"{ACCESS_COOKIE}="))
    refresh = next(c for c in cookies if c.startswith(f"{REFRESH_COOKIE}="))

    # HttpOnly на обеих; refresh ограничена путём /auth, access — корнем
    assert "HttpOnly" in access and "HttpOnly" in refresh
    assert "Path=/" in access
    assert f"Path={settings.api_v1_prefix}/auth" in refresh
    assert "acc" in access and "ref" in refresh


def test_clear_auth_cookies_expires_both():
    resp = Response()
    clear_auth_cookies(resp)
    cookies = _set_cookie_headers(resp)
    assert any(c.startswith(f"{ACCESS_COOKIE}=") for c in cookies)
    assert any(c.startswith(f"{REFRESH_COOKIE}=") for c in cookies)
    # delete_cookie выставляет истёкший срок (Max-Age=0 или прошедший Expires)
    assert all("Max-Age=0" in c or "expires=" in c.lower() for c in cookies)


def _request_with_cookie(token: str) -> Request:
    header = f"{ACCESS_COOKIE}={token}".encode()
    return Request({"type": "http", "headers": [(b"cookie", header)]})


async def test_cookie_mode_reads_access_from_cookie(monkeypatch, fake_redis):
    monkeypatch.setattr(settings, "auth_token_transport", AuthTransport.COOKIE)
    monkeypatch.setattr(settings, "auth_validate_session", False)

    token = create_access_token("11111111-1111-1111-1111-111111111111", Role.USER, "sid-1")
    user = await get_current_user(
        request=_request_with_cookie(token),
        credentials=None,  # заголовка Authorization нет — токен только в куке
        sessions=SessionStore(fake_redis),
    )
    assert str(user.id) == "11111111-1111-1111-1111-111111111111"
    assert user.role == Role.USER
    assert user.sid == "sid-1"


async def test_header_mode_ignores_cookie(monkeypatch, fake_redis):
    # header-режим: кука есть, но читаем ТОЛЬКО заголовок -> без него отказ
    monkeypatch.setattr(settings, "auth_token_transport", AuthTransport.HEADER)
    token = create_access_token("11111111-1111-1111-1111-111111111111", Role.USER, "sid-1")
    with pytest.raises(UnauthorizedError, match="Missing access token"):
        await get_current_user(
            request=_request_with_cookie(token),
            credentials=None,
            sessions=SessionStore(fake_redis),
        )


def test_cookie_transport_incompatible_with_global_key():
    # Валидатор: cookie + глобальный ключ => падаем на старте
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="несовместим"):
        Settings(_env_file=None, auth_token_transport="cookie", global_api_key_enabled=True)


def test_cookie_transport_requires_jwt():
    from pydantic import ValidationError

    # global_api_key_enabled=True — чтобы пройти _validate_auth_modes (jwt off => нужен gate)
    with pytest.raises(ValidationError, match="AUTH_JWT_ENABLED"):
        Settings(
            _env_file=None,
            auth_token_transport="cookie",
            auth_jwt_enabled=False,
            global_api_key_enabled=True,
        )
