"""
Базовый репозиторий (Repository Pattern).

Репозиторий ОТВЕЧАЕТ ТОЛЬКО за доступ к данным — никакой бизнес-логики.
Работает с переданной AsyncSession (её жизненным циклом владеет UoW).

Generic по модели. Для hot paths предусмотрены:
* stream() — серверный курсор (yield по строкам), не тянет всё в память.
* выборки используют ORM, но репозиторий — место, где допустим raw SQL
  (см. UserRepository.search_raw как пример).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, entity_id: uuid.UUID) -> ModelT | None:
        # session.get использует identity map — дешёвый primary-key lookup
        return await self.session.get(self.model, entity_id)

    async def get_by(self, **filters: Any) -> ModelT | None:
        stmt = select(self.model).filter_by(**filters).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        order_by: Any | None = None,
    ) -> Sequence[ModelT]:
        stmt = select(self.model).limit(limit).offset(offset)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        return (await self.session.execute(stmt)).scalars().all()

    async def count(self, **filters: Any) -> int:
        stmt = select(func.count()).select_from(self.model)
        if filters:
            stmt = stmt.filter_by(**filters)
        return (await self.session.execute(stmt)).scalar_one()

    async def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        # flush — получаем сгенерированные id/значения без commit (commit делает UoW)
        await self.session.flush()
        return entity

    async def update_by_id(self, entity_id: uuid.UUID, **values: Any) -> int:
        stmt = update(self.model).where(self.model.id == entity_id).values(**values)  # type: ignore[attr-defined]
        result = await self.session.execute(stmt)
        return result.rowcount  # type: ignore[attr-defined]  # CursorResult у DML

    async def delete_by_id(self, entity_id: uuid.UUID) -> int:
        stmt = delete(self.model).where(self.model.id == entity_id)  # type: ignore[attr-defined]
        result = await self.session.execute(stmt)
        return result.rowcount  # type: ignore[attr-defined]  # CursorResult у DML

    async def stream(
        self,
        *,
        batch_size: int = 1000,
        order_by: Any | None = None,
    ) -> AsyncIterator[ModelT]:
        """
        Потоковая итерация по таблице через серверный курсор.
        Использует yield — память O(batch_size), а не O(всей таблицы).
        Подходит для экспорта/обработки больших объёмов под нагрузкой.
        """
        stmt = select(self.model)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        stream = await self.session.stream(stmt.execution_options(yield_per=batch_size))
        async for row in stream.scalars():
            yield row
