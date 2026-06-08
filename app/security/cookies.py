"""
HttpOnly-куки для токенов авторизации.

Включается режимом AUTH_TOKEN_TRANSPORT=cookie. На login/refresh кладём access/refresh
в куки (HttpOnly => недоступны JS, защита от кражи токена через XSS), на logout — чистим.

Параметры безопасности:
* HttpOnly — всегда (JS не читает токен);
* Secure — по флагу (в проде true: только HTTPS);
* SameSite — lax по умолчанию (куки не уходят на кросс-сайтовый POST => базовая защита
  от CSRF). Для кросс-доменного фронта нужен SameSite=none + Secure (+ CSRF-токены).

ВАЖНО (практика): куки тут — для фронта на ТОМ ЖЕ домене. Кросс-доменные куки НЕ работают
в Safari/iOS из-за ITP (Intelligent Tracking Prevention) — он режет third-party куки даже
с SameSite=none; Secure. Для кросс-доменного фронта используйте header-режим (Bearer):
там нет ни CSRF, ни проблем с Safari.

Refresh-кука ограничена путём /auth — браузер шлёт её только на ручки авторизации,
а не на каждый запрос (меньше поверхность утечки).
"""

from __future__ import annotations

from typing import Literal, cast

from fastapi import Response

from app.core.config import settings
from app.schemas.auth import TokenPair

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"


def _refresh_path() -> str:
    # Refresh-кука уходит только на /api/v1/auth/* (там, где она нужна)
    return f"{settings.api_v1_prefix}/auth"


def _samesite() -> Literal["lax", "strict", "none"]:
    value = settings.auth_cookie_samesite.lower()
    if value not in {"lax", "strict", "none"}:
        value = "lax"
    return cast(Literal["lax", "strict", "none"], value)


def set_auth_cookies(response: Response, pair: TokenPair) -> None:
    """Положить access/refresh в HttpOnly-куки (срок = срок жизни соответствующего токена)."""
    same = _samesite()
    secure = settings.auth_cookie_secure
    response.set_cookie(
        ACCESS_COOKIE,
        pair.access_token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,
        secure=secure,
        samesite=same,
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        pair.refresh_token,
        max_age=settings.refresh_token_expire_days * 86400,
        httponly=True,
        secure=secure,
        samesite=same,
        path=_refresh_path(),
    )


def clear_auth_cookies(response: Response) -> None:
    """Удалить куки токенов (параметры path/samesite/secure должны совпадать с set_cookie)."""
    same = _samesite()
    secure = settings.auth_cookie_secure
    response.delete_cookie(ACCESS_COOKIE, path="/", httponly=True, secure=secure, samesite=same)
    response.delete_cookie(
        REFRESH_COOKIE, path=_refresh_path(), httponly=True, secure=secure, samesite=same
    )
