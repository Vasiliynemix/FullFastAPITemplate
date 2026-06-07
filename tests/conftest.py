"""
Общие фикстуры тестов.

Подход:
* БД — async SQLite в памяти (aiosqlite): быстрые интеграционные тесты репозиториев
  без поднятия Postgres. Схема создаётся из metadata.
* Redis — fakeredis (in-memory), мокает кэш/rate-limit/idempotency.
* Broker — InMemoryBroker (он же используется в проде по умолчанию для memory-режима).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.broker.memory import InMemoryBroker
from app.models.base import Base


@pytest_asyncio.fixture
async def engine() -> AsyncIterator:
    # StaticPool + один shared connection — все сессии видят одну in-memory БД
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def sessionmaker(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def session(sessionmaker) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as s:
        yield s


@pytest.fixture
def fake_cache():
    """Лёгкий in-memory кэш, совместимый с AbstractCache."""
    from app.cache.base import AbstractCache

    class FakeCache(AbstractCache):
        def __init__(self) -> None:
            self._store: dict = {}

        async def get(self, key: str):
            return self._store.get(key)

        async def set(self, key: str, value, *, ttl=None) -> None:
            self._store[key] = value

        async def delete(self, *keys: str) -> int:
            n = 0
            for k in keys:
                n += 1 if self._store.pop(k, None) is not None else 0
            return n

        async def exists(self, key: str) -> bool:
            return key in self._store

        async def incr(self, key: str, *, amount: int = 1, ttl=None) -> int:
            self._store[key] = int(self._store.get(key, 0)) + amount
            return self._store[key]

    return FakeCache()


@pytest.fixture
def memory_broker() -> InMemoryBroker:
    return InMemoryBroker()


@pytest.fixture
def fake_storage():
    """In-memory реализация AbstractStorage — для тестов без S3."""
    from app.storage.base import AbstractStorage, ObjectNotFoundError

    class FakeStorage(AbstractStorage):
        def __init__(self) -> None:
            self._d: dict[str, bytes] = {}

        async def put(self, key: str, data: bytes, *, content_type: str | None = None) -> None:
            self._d[key] = data

        async def get(self, key: str) -> bytes:
            if key not in self._d:
                raise ObjectNotFoundError(key)
            return self._d[key]

        async def stream(self, key: str, *, chunk_size: int = 1024):
            if key not in self._d:
                raise ObjectNotFoundError(key)
            data = self._d[key]
            for i in range(0, len(data), chunk_size):
                yield data[i : i + chunk_size]

        async def delete(self, key: str) -> None:
            self._d.pop(key, None)

        async def exists(self, key: str) -> bool:
            return key in self._d

        async def list(self, prefix: str = "") -> list[str]:
            return sorted(k for k in self._d if k.startswith(prefix))

        async def presigned_url(self, key: str, *, expires: int = 3600, method: str = "GET") -> str:
            return f"/api/v1/files/download/{key}"

    return FakeStorage()


@pytest_asyncio.fixture
async def fake_redis():
    """In-memory Redis (fakeredis) для refresh-store / rate limit / idempotency."""
    from fakeredis import aioredis

    client = aioredis.FakeRedis()
    yield client
    await client.aclose()
