"""Pydantic-схемы пользователя (DTO между API и сервисом)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.security.roles import Role


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None


class UserRead(BaseModel):
    # from_attributes — сборка прямо из ORM-объекта без ручного маппинга
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str
    is_active: bool
    role: Role
