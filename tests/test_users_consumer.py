"""Консьюмеры UserCreated: fan-out (одно событие -> много обработчиков) + аудит-метрика."""

from __future__ import annotations

import pytest

from app.broker.events import EventBus
from app.broker.memory import InMemoryBroker
from app.cache.redis_cache import RedisCache
from app.consumers import register_consumers
from app.consumers.users import handle_user_created_audit
from app.services.user import UserCreated

pytestmark = pytest.mark.asyncio


async def test_fanout_one_event_many_handlers():
    broker = InMemoryBroker()
    bus = EventBus(broker)
    a: list[UserCreated] = []
    b: list[UserCreated] = []

    async def handler_a(event: UserCreated) -> None:
        a.append(event)

    async def handler_b(event: UserCreated) -> None:
        b.append(event)

    # Два независимых обработчика на ОДНО событие
    await bus.subscribe(UserCreated, handler_a)
    await bus.subscribe(UserCreated, handler_b)
    await bus.publish(UserCreated(id="u1", email="a@example.com"))
    await broker.disconnect()

    # in-memory брокер делает настоящий fan-out: оба получили событие
    assert len(a) == 1 and len(b) == 1
    assert a[0].id == "u1" and b[0].id == "u1"


async def test_audit_handler_increments_metric(monkeypatch, fake_redis):
    cache = RedisCache(fake_redis)
    monkeypatch.setattr("app.consumers.users.get_redis_cache", lambda: cache)

    await handle_user_created_audit(UserCreated(id="u1", email="a@example.com"))
    await handle_user_created_audit(UserCreated(id="u2", email="b@example.com"))

    assert await cache.get("stats:users_created") == 2


async def test_register_consumers_wires_user_handlers(monkeypatch, fake_redis):
    # Полный путь: publish UserCreated -> оба user-обработчика отработали
    cache = RedisCache(fake_redis)
    monkeypatch.setattr("app.consumers.users.get_redis_cache", lambda: cache)

    broker = InMemoryBroker()
    bus = EventBus(broker)
    await register_consumers(bus)
    await bus.publish(UserCreated(id="u1", email="a@example.com"))
    await broker.disconnect()

    # audit-обработчик увеличил метрику регистраций
    assert await cache.get("stats:users_created") == 1
