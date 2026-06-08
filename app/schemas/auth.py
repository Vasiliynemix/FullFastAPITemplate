"""Схемы аутентификации (DTO для ручек /auth)."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field

from app.security.roles import Role


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=255)
    # Роль опциональна; по умолчанию обычный пользователь.
    # В реальном проде назначение привилегированных ролей стоит закрыть отдельно.
    role: Role = Role.USER


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    # Опционально: если включены auth-куки, токен берётся из refresh-куки (тело можно пустым)
    refresh_token: str | None = None


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # срок жизни access в секундах


class SessionInfo(BaseModel):
    sid: str
    created_at: str | None = None
    last_used_at: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    current: bool = False  # это текущая сессия (по которой пришёл запрос)
