"""
Базовый репозиторий (Repository Pattern).

Репозиторий ОТВЕЧАЕТ ТОЛЬКО за доступ к данным — никакой бизнес-логики.
Работает с переданной AsyncSession (её жизненным циклом владеет UoW).

Generic по модели. Для hot paths предусмотрены:
* stream() — серверный курсор (yield по строкам), не тянет всё в память.
* выборки используют ORM, но репозиторий — место, где допустим raw SQL
  (см. UserRepository.search_raw как пример).

Eager-load relationship: геттеры принимают `options=[selectinload(Model.rel), ...]`.
В async ленивая загрузка связи НЕВОЗМОЖНА (падает с MissingGreenlet при доступе к
атрибуту вне сессии), поэтому всё, что будешь сериализовать/использовать, грузи заранее.
Примеры — AccountRepository / app/services/account.py (в т.ч. вложенный selectinload).

Защита от гонок (lost update в read-modify-write): геттеры принимают `for_update=True`
(`SELECT ... FOR UPDATE`) — пессимистичная блокировка строки до конца транзакции.
Использовать ТОЛЬКО внутри `async with uow` и НЕ держать лок во время медленного I/O.
Доп. опции: `skip_locked` (пропустить занятые — для очередей), `nowait` (сразу падать,
если занято). Пример:
    async with uow:
        acc = await uow.accounts.get(acc_id, for_update=True)  # строка заблокирована
        acc.balance -= 10                                       # конкуренты ждут commit
        await uow.commit()
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.query import apply_filters, apply_search, apply_sort
from app.models.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]
    # Колонки, по которым ищет q (умный поиск). Переопредели в наследнике, напр. ("full_name",).
    search_fields: tuple[str, ...] = ()

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _dialect(self) -> str:
        return self.session.get_bind().dialect.name  # postgresql | sqlite | ...

    async def paginate(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        filters: dict[str, str] | None = None,
        sort: str | None = None,
        q: str | None = None,
        options: Sequence[Any] | None = None,
    ) -> tuple[Sequence[ModelT], int]:
        """
        Универсальный список: фильтры (field__op) + сортировка + умный поиск q. Возвращает
        (страница, total). Поля валидируются по колонкам модели (см. app/db/query.py).

        options — loader-опции (selectinload/...) для eager-загрузки relationship элементов
        страницы (в async обязательны, если сериализуешь связи). Применяются ТОЛЬКО к выборке
        элементов, а не к COUNT (чтобы не тянуть связи в подсчёте).
        """
        stmt = select(self.model)
        if filters:
            stmt = apply_filters(stmt, self.model, filters)
        order = None
        if q and self.search_fields:
            stmt, order = apply_search(
                stmt,
                self.model,
                q,
                list(self.search_fields),
                dialect=self._dialect(),
                threshold=settings.search_similarity_threshold,
            )
        # total — после фильтров/поиска, до eager-load/сортировки/пагинации
        total = (
            await self.session.execute(select(func.count()).select_from(stmt.subquery()))
        ).scalar_one()
        # eager-load — только на выборку элементов (не на COUNT выше)
        if options:
            stmt = stmt.options(*options)
        # при поиске сортируем по релевантности, иначе — sort/дефолт (created_at asc)
        stmt = stmt.order_by(order) if order is not None else apply_sort(stmt, self.model, sort)
        stmt = stmt.limit(per_page).offset((page - 1) * per_page)
        items = (await self.session.execute(stmt)).scalars().all()
        return items, total

    @staticmethod
    def _lock(stmt: Any, *, for_update: bool, nowait: bool, skip_locked: bool) -> Any:
        # Навешивает SELECT ... FOR UPDATE [NOWAIT | SKIP LOCKED] на выборку.
        # На SQLite (тесты) диалект молча игнорирует — ошибки не будет.
        if for_update:
            stmt = stmt.with_for_update(nowait=nowait, skip_locked=skip_locked)
        return stmt

    async def get(
        self,
        entity_id: uuid.UUID,
        *,
        for_update: bool = False,
        nowait: bool = False,
        skip_locked: bool = False,
        options: Sequence[Any] | None = None,
    ) -> ModelT | None:
        # options — loader-опции (selectinload/joinedload/...) для eager-загрузки relationship.
        # В async ленивая загрузка падает с MissingGreenlet, поэтому связи грузим заранее.
        if not for_update and not options:
            # session.get использует identity map — дешёвый primary-key lookup
            return await self.session.get(self.model, entity_id)
        stmt = select(self.model).where(self.model.id == entity_id)  # type: ignore[attr-defined]
        if options:
            stmt = stmt.options(*options)
        stmt = self._lock(stmt, for_update=for_update, nowait=nowait, skip_locked=skip_locked)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by(
        self,
        *,
        for_update: bool = False,
        nowait: bool = False,
        skip_locked: bool = False,
        options: Sequence[Any] | None = None,
        **filters: Any,
    ) -> ModelT | None:
        stmt = select(self.model).filter_by(**filters).limit(1)
        if options:
            stmt = stmt.options(*options)
        stmt = self._lock(stmt, for_update=for_update, nowait=nowait, skip_locked=skip_locked)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        order_by: Any | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
        options: Sequence[Any] | None = None,
    ) -> Sequence[ModelT]:
        """
        НИЗКОУРОВНЕВЫЙ примитив выборки: limit/offset + order_by, без COUNT и без total.
        Для внутренних батч-операций и очередей (`for_update=True, skip_locked=True` —
        «забрать пачку, минуя занятые строки»).

        Для СПИСОЧНЫХ РУЧЕК используй paginate() — он даёт total + фильтры (field__op) +
        сортировку + умный поиск q. Здесь этого намеренно нет (это «голая» выборка).
        """
        stmt = select(self.model).limit(limit).offset(offset)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        if options:
            stmt = stmt.options(*options)
        # for_update на списке + skip_locked — типовой паттерн «забрать пачку из очереди»
        stmt = self._lock(stmt, for_update=for_update, nowait=False, skip_locked=skip_locked)
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
