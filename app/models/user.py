"""Доменная модель User — пример сущности + принципал аутентификации."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, VersionedMixin, trgm_index
from app.security.roles import DEFAULT_ROLE, Role

if TYPE_CHECKING:
    from app.models.account import Account
    from app.models.profile import Profile


class User(UUIDPrimaryKeyMixin, TimestampMixin, VersionedMixin, Base):
    __tablename__ = "users"

    # GIN-триграммные индексы под умный поиск (search_fields в UserRepository).
    # Объявлены декларативно — миграция создаётся автогеном/op.create_index, без сырого SQL.
    __table_args__ = (
        trgm_index("ix_users_full_name_trgm", "full_name"),
        trgm_index("ix_users_email_trgm", "email"),
    )

    # index=True на email — ускоряет частые выборки/логин по email (hot path)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    # nullable: пользователь может существовать без пароля (например service-аккаунт
    # или приглашение до установки пароля). Логин для таких просто невозможен.
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Роль хранится как строка из enum Role. Менять набор ролей — в app/security/roles.py
    role: Mapped[Role] = mapped_column(String(32), default=DEFAULT_ROLE, nullable=False)

    # one-to-one: у юзера один профиль (uselist=False)
    profile: Mapped[Profile | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    # one-to-many: у юзера много счетов
    accounts: Mapped[list[Account]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
