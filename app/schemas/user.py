"""Pydantic-схемы пользователя (DTO между API и сервисом)."""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.account import AccountRead, ProfileRead
from app.security.roles import Role


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None


class UserRead(BaseModel):
    # from_attributes — сборка прямо из ORM-объекта без ручного маппинга.
    # БАЗОВАЯ форма без связей: model_validate читает только эти поля и НЕ трогает
    # relationship'ы, поэтому годится без eager-load (create/update/stream/register).
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str
    is_active: bool
    role: Role
    created_at: datetime.datetime
    updated_at: datetime.datetime


class UserReadDetail(UserRead):
    """UserRead + связи (accounts 1-many, profile 1-1) — для ДЕТАЛЬНЫХ ответов
    (GET /users/{id}, список), где связи реально показываются. Собирать только из
    eager-загруженного ORM-объекта, иначе ленивый доступ к связям в async уронит
    MissingGreenlet. Базовый UserRead связи не грузит (не везде нужны)."""

    accounts: list[AccountRead]
    profile: ProfileRead | None = None
