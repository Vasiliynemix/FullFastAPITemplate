"""Доменная модель User — пример сущности + принципал аутентификации."""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.security.roles import DEFAULT_ROLE, Role


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    # index=True на email — ускоряет частые выборки/логин по email (hot path)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    # nullable: пользователь может существовать без пароля (например service-аккаунт
    # или приглашение до установки пароля). Логин для таких просто невозможен.
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Роль хранится как строка из enum Role. Менять набор ролей — в app/security/roles.py
    role: Mapped[Role] = mapped_column(String(32), default=DEFAULT_ROLE, nullable=False)
