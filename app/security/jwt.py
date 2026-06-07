"""
JWT: выпуск и проверка access/refresh токенов (PyJWT).

Дизайн:
* access — короткоживущий, несёт sub (id пользователя), role, type=access.
  Проверяется stateless на каждом запросе (быстро, без обращения к БД/Redis).
* refresh — долгоживущий, несёт sub, type=refresh, sid (сессия) и jti.
  sid/jti управляются SessionStore (Redis) — ротация, reuse-detection, отзыв.

Все временные метки в UTC. Клеймы exp/iat/nbf проверяются PyJWT автоматически.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass
from enum import StrEnum

import jwt

from app.core.config import settings
from app.security.roles import Role


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


class TokenError(Exception):
    """Невалидный/просроченный/неподходящего типа токен."""


@dataclass(slots=True)
class TokenPayload:
    sub: str  # id пользователя
    type: TokenType
    role: Role | None
    sid: str | None  # id сессии (общий для access и refresh одной сессии)
    jti: str | None  # id конкретного refresh-токена (для ротации/reuse)
    exp: int


def _now() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.UTC)


def _encode(claims: dict) -> str:
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(subject: str | uuid.UUID, role: Role, sid: str) -> str:
    now = _now()
    claims = {
        "sub": str(subject),
        "role": str(role),
        "type": TokenType.ACCESS.value,
        "sid": sid,
        "iat": now,
        "exp": now + datetime.timedelta(minutes=settings.access_token_expire_minutes),
    }
    return _encode(claims)


def create_refresh_token(subject: str | uuid.UUID, sid: str, jti: str) -> str:
    """jti/sid генерирует и хранит SessionStore — здесь только кодируем."""
    now = _now()
    claims = {
        "sub": str(subject),
        "type": TokenType.REFRESH.value,
        "sid": sid,
        "jti": jti,
        "iat": now,
        "exp": now + datetime.timedelta(days=settings.refresh_token_expire_days),
    }
    return _encode(claims)


def decode_token(token: str, *, expected_type: TokenType) -> TokenPayload:
    try:
        raw = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("Invalid token") from exc

    if raw.get("type") != expected_type.value:
        raise TokenError("Wrong token type")

    role_raw = raw.get("role")
    return TokenPayload(
        sub=raw["sub"],
        type=TokenType(raw["type"]),
        role=Role(role_raw) if role_raw else None,
        sid=raw.get("sid"),
        jti=raw.get("jti"),
        exp=raw["exp"],
    )
