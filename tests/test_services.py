"""
Тесты сервисного слоя.

Изолируем БД (async SQLite), кэш (fake), брокер (in-memory). Проверяем бизнес-
логику: создание, конфликт email, чтение из кэша, инвалидацию, публикацию события.
"""

from __future__ import annotations

import uuid

import pytest

from app.broker.events import EventBus
from app.db.uow import UnitOfWork
from app.exceptions.base import ConflictError, NotFoundError
from app.outbox.relay import OutboxRelay
from app.schemas.user import UserCreate, UserUpdate
from app.services.user import UserCreated, UserService

pytestmark = pytest.mark.asyncio


def _service(sessionmaker, cache, broker=None) -> UserService:
    # broker больше не нужен сервису: UserCreated идёт через outbox (см. тест ниже)
    return UserService(uow=UnitOfWork(sessionmaker), cache=cache)


async def test_create_writes_outbox_then_relay_publishes(sessionmaker, fake_cache, memory_broker):
    got: list[UserCreated] = []

    async def handler(event: UserCreated) -> None:  # типизированное событие
        got.append(event)

    bus = EventBus(memory_broker)
    await bus.subscribe(UserCreated, handler)

    svc = UserService(uow=UnitOfWork(sessionmaker), cache=fake_cache)
    dto = await svc.create(UserCreate(email="x@example.com", full_name="X"))
    assert dto.email == "x@example.com"

    # Событие пока ТОЛЬКО в outbox — в брокер ещё ничего не ушло
    async with UnitOfWork(sessionmaker) as uow:
        pending = await uow.outbox.fetch_unpublished(limit=10)
    assert len(pending) == 1
    assert pending[0].topic == UserCreated.topic
    assert len(got) == 0

    # Релей публикует -> подписчик получает типизированное событие
    relay = OutboxRelay(lambda: UnitOfWork(sessionmaker), memory_broker)
    sent = await relay.run_once()
    await memory_broker.disconnect()

    assert sent == 1
    assert len(got) == 1
    assert got[0].email == "x@example.com"
    assert got[0].id == str(dto.id)

    # После публикации outbox пуст (строка помечена published_at)
    async with UnitOfWork(sessionmaker) as uow:
        assert len(await uow.outbox.fetch_unpublished(limit=10)) == 0


async def test_create_duplicate_raises_conflict(sessionmaker, fake_cache, memory_broker):
    svc = _service(sessionmaker, fake_cache, memory_broker)
    await svc.create(UserCreate(email="dup@example.com", full_name="A"))
    with pytest.raises(ConflictError):
        await svc.create(UserCreate(email="dup@example.com", full_name="B"))


async def test_get_uses_cache(sessionmaker, fake_cache, memory_broker):
    svc = _service(sessionmaker, fake_cache, memory_broker)
    created = await svc.create(UserCreate(email="cache@example.com", full_name="C"))

    # После create dto уже в кэше — get должен вернуть его без обращения к БД
    assert await fake_cache.exists(f"user:{created.id}")
    fetched = await svc.get(created.id)
    assert fetched.id == created.id


async def test_update_invalidates_cache(sessionmaker, fake_cache, memory_broker):
    svc = _service(sessionmaker, fake_cache, memory_broker)
    created = await svc.create(UserCreate(email="upd@example.com", full_name="Old"))

    await svc.update(created.id, UserUpdate(full_name="New"))
    # Кэш по ключу должен быть сброшен
    assert not await fake_cache.exists(f"user:{created.id}")

    fetched = await svc.get(created.id)
    assert fetched.full_name == "New"


async def test_get_missing_raises_not_found(sessionmaker, fake_cache, memory_broker):
    svc = _service(sessionmaker, fake_cache, memory_broker)
    with pytest.raises(NotFoundError):
        await svc.get(uuid.uuid4())


async def test_delete_then_get_raises(sessionmaker, fake_cache, memory_broker):
    svc = _service(sessionmaker, fake_cache, memory_broker)
    created = await svc.create(UserCreate(email="del@example.com", full_name="D"))
    await svc.delete(created.id)
    with pytest.raises(NotFoundError):
        await svc.get(created.id)
