"""Репозиторий пользователей. Демонстрирует ORM + пример raw SQL для hot path."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from app.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_email(self, email: str) -> User | None:
        return await self.get_by(email=email)

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
