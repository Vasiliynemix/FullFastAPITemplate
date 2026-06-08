"""
Unit of Work (UoW).

Инкапсулирует одну транзакцию и набор репозиториев поверх одной сессии.
Сервисный слой работает ТОЛЬКО через UoW — он не знает про сессии/commit напрямую.

Использование:
    async with UnitOfWork(sessionmaker) as uow:
        user = await uow.users.add(User(...))     # частые репо — именованные property
        cat = await uow.repo(CategoryRepository).get(cid)  # любой репо — дженериком
        await uow.commit()        # явный commit
    # при выходе без commit — автоматический rollback

Репозитории создаются ЛЕНИВО (при первом обращении) и кэшируются на время транзакции.
Кэш сбрасывается на каждый вход в контекст — UoW можно переиспользовать между
`async with` (репо не повиснут на старой закрытой сессии).

Преимущества под нагрузкой: одна сессия/транзакция на бизнес-операцию,
предсказуемое освобождение соединения обратно в пул.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.account import (
    AccountRepository,
    CategoryRepository,
    TransactionRepository,
)
from app.repositories.base import BaseRepository
from app.repositories.outbox import OutboxRepository
from app.repositories.user import UserRepository

R = TypeVar("R", bound=BaseRepository[Any])


class UnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._repos: dict[type[Any], Any] = {}

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("UnitOfWork used outside of `async with` context")
        return self._session

    def repo(self, repo_cls: type[R]) -> R:
        """
        Лениво создать/получить репозиторий по его классу. Кэш — на время транзакции.
        Для любого репозитория, в т.ч. не объявленного именованным property ниже:
            await uow.repo(MyRepository).get(...)
        """
        cached = self._repos.get(repo_cls)
        if cached is None:
            cached = repo_cls(self.session)
            self._repos[repo_cls] = cached
        return cached  # type: ignore[no-any-return]

    # Именованные property для частых репозиториев — эргономика + автокомплит/типы.
    # Под капотом — тот же ленивый кэш (repo()), создаётся при первом обращении.
    @property
    def users(self) -> UserRepository:
        return self.repo(UserRepository)

    @property
    def outbox(self) -> OutboxRepository:
        return self.repo(OutboxRepository)

    @property
    def accounts(self) -> AccountRepository:
        return self.repo(AccountRepository)

    @property
    def transactions(self) -> TransactionRepository:
        return self.repo(TransactionRepository)

    @property
    def categories(self) -> CategoryRepository:
        return self.repo(CategoryRepository)

    async def __aenter__(self) -> UnitOfWork:
        # Один UoW = одна транзакция за раз. Параллельный/вложенный вход на ОДНОМ инстансе
        # затёр бы _session/_repos -> для конкурентных транзакций создавайте отдельные UoW
        # (своя сессия у каждого). AsyncSession и сама не допускает конкурентного доступа.
        if self._session is not None:
            raise RuntimeError(
                "UnitOfWork уже активен: он не реентерабельный и не concurrency-safe. "
                "Для параллельных транзакций используйте отдельный UnitOfWork на каждую."
            )
        self._session = self._session_factory()
        self._repos.clear()  # новая сессия => сбрасываем кэш репозиториев
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
            self._repos.clear()

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()
