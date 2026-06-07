"""
Outbox-сообщение (transactional outbox).

Событие пишется в эту таблицу В ТОЙ ЖЕ транзакции, что и бизнес-данные. Отдельный
релей (app/outbox/relay.py) периодически публикует неопубликованные строки в брокер
и проставляет published_at.

Гарантия: если бизнес-транзакция зафиксирована — событие НЕ потеряется (at-least-once);
если откатилась — события тоже нет. Это закрывает дыру «commit прошёл, а publish в
брокер упал → событие потеряно».
"""

from __future__ import annotations

import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin

# JSONB на Postgres (бинарный, индексируемый), обычный JSON на SQLite (тесты)
_JSON = JSON().with_variant(JSONB(), "postgresql")


class OutboxMessage(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "outbox_messages"

    # Партиальный индекс ровно под запрос релея: «неопубликованные по порядку».
    # postgresql_where игнорируется на SQLite (там будет обычный индекс) — это ок.
    __table_args__ = (
        Index(
            "ix_outbox_unpublished",
            "created_at",
            postgresql_where=text("published_at IS NULL"),
        ),
    )

    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict] = mapped_column(_JSON, nullable=False)
    key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(), server_default=func.now(), nullable=False
    )
    # NULL => ещё не опубликовано (по этому условию выбирает релей).
    published_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(), nullable=True)
    # Счётчик попыток публикации — для наблюдаемости/отладки «застрявших» строк.
    attempts: Mapped[int] = mapped_column(Integer(), server_default="0", nullable=False)
