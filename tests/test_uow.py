"""UnitOfWork: ленивое создание репозиториев + сброс кэша между контекстами."""

from __future__ import annotations

import pytest

from app.db.uow import UnitOfWork
from app.repositories.user import UserRepository

pytestmark = pytest.mark.asyncio


async def test_repo_cached_within_context(sessionmaker):
    async with UnitOfWork(sessionmaker) as uow:
        a = uow.repo(UserRepository)
        b = uow.repo(UserRepository)
        assert a is b  # один инстанс на транзакцию
        assert uow.users is a  # именованный property — тот же кэш


async def test_repo_reset_between_contexts(sessionmaker):
    uow = UnitOfWork(sessionmaker)  # переиспользуем ОДИН UoW
    async with uow:
        first = uow.users
    async with uow:
        second = uow.users
    # новый вход => новая сессия => новый репозиторий (не повис на старой)
    assert first is not second


async def test_repo_outside_context_raises(sessionmaker):
    uow = UnitOfWork(sessionmaker)
    with pytest.raises(RuntimeError, match="outside"):
        _ = uow.users  # доступ к репо => к session вне `async with`


async def test_reentry_on_same_instance_raises(sessionmaker):
    # Вложенный/параллельный вход на ОДНОМ инстансе запрещён (затёр бы состояние)
    uow = UnitOfWork(sessionmaker)
    async with uow:
        with pytest.raises(RuntimeError, match="уже активен"):
            async with uow:
                pass
