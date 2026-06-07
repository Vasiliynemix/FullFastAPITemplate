"""
Unit of Work (UoW).

Инкапсулирует одну транзакцию и набор репозиториев поверх одной сессии.
Сервисный слой работает ТОЛЬКО через UoW — он не знает про сессии/commit напрямую.

Использование:
    async with UnitOfWork(sessionmaker) as uow:
        user = await uow.users.add(User(...))
        await uow.commit()        # явный commit
    # при выходе без commit — автоматический rollback

Преимущества под нагрузкой: одна сессия/транзакция на бизнес-операцию,
предсказуемое освобождение соединения обратно в пул.
"""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.outbox import OutboxRepository
from app.repositories.user import UserRepository


class UnitOfWork:
    users: UserRepository

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("UnitOfWork used outside of `async with` context")
        return self._session

    async def __aenter__(self) -> UnitOfWork:
        self._session = self._session_factory()
        # Регистрируем репозитории на текущую сессию
        self.users = UserRepository(self._session)
        self.outbox = OutboxRepository(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if exc_type is not None:
                await self.rollback()
        finally:
            await self.session.close()
            self._session = None

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()
