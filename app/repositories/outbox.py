"""
Репозиторий outbox — доступ к таблице outbox_messages.

add_event() — положить событие в текущую транзакцию UoW (рядом с бизнес-данными).
Остальное — для релея: выбрать неопубликованные, пометить отправленные, подчистить старьё.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Sequence

from sqlalchemy import delete, func, select, update

from app.broker.events import Event
from app.models.outbox import OutboxMessage
from app.repositories.base import BaseRepository


class OutboxRepository(BaseRepository[OutboxMessage]):
    model = OutboxMessage

    async def add_event(self, event: Event, *, key: str | None = None) -> None:
        """Записать событие в outbox (НЕ коммитит — commit делает UoW вместе с бизнес-данными)."""
        self.session.add(
            OutboxMessage(topic=event.topic, payload=event.model_dump(mode="json"), key=key)
        )

    async def fetch_unpublished(self, *, limit: int) -> Sequence[OutboxMessage]:
        """Неопубликованные строки в порядке создания (использует партиальный индекс)."""
        stmt = (
            select(OutboxMessage)
            .where(OutboxMessage.published_at.is_(None))
            .order_by(OutboxMessage.created_at)
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def mark_published(self, ids: Sequence[uuid.UUID]) -> None:
        """Пометить строки опубликованными (published_at = now на стороне БД)."""
        if not ids:
            return
        stmt = (
            update(OutboxMessage)
            .where(OutboxMessage.id.in_(ids))
            .values(published_at=func.now(), attempts=OutboxMessage.attempts + 1)
        )
        await self.session.execute(stmt)

    async def delete_published_before(self, cutoff: datetime.datetime) -> int:
        """Удалить опубликованные строки старше cutoff (retention)."""
        stmt = delete(OutboxMessage).where(
            OutboxMessage.published_at.is_not(None),
            OutboxMessage.published_at < cutoff,
        )
        return (await self.session.execute(stmt)).rowcount  # type: ignore[attr-defined]
