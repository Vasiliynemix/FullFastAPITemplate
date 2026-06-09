"""Репозиторий пользователей. Демонстрирует ORM + пример raw SQL для hot path."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.orm import selectinload

from app.models.account import Account
from app.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User
    # Поля для умного поиска q (pg_trgm / ILIKE)
    search_fields = ("full_name", "email")

    async def get_by_email(self, email: str) -> User | None:
        return await self.get_by(email=email)

    async def get_overview(self, user_id: uuid.UUID) -> User | None:
        # Вложенный eager-load: профиль (one-to-one) + счета (one-to-many) + их транзакции.
        # Так грузятся «транзакции юзера» через два уровня связей одним набором запросов.
        return await self.get(
            user_id,
            options=[
                selectinload(User.profile),
                selectinload(User.accounts).selectinload(Account.transactions),
            ],
        )

    async def search_raw(self, query: str, *, limit: int = 20) -> Sequence[dict]:
        """
        Пример сырого SQL в hot path: легковесный поиск по префиксу email.

        Возвращаем mappings (dict) минуя ORM-гидрацию — это снижает оверхед,
        когда нужен только список полей, а не полноценные сущности.
        Параметры биндятся (:q) — никаких конкатенаций, защита от инъекций.
        """
        stmt = text(
            """
            SELECT id, email, full_name, is_active
            FROM users
            WHERE email ILIKE :q
            ORDER BY email
            LIMIT :limit
            """
        )
        result = await self.session.execute(stmt, {"q": f"{query}%", "limit": limit})
        return [dict(row) for row in result.mappings().all()]
