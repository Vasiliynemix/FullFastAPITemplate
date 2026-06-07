"""Тесты transactional outbox: запись события, релей, at-least-once, retention-чистка."""

from __future__ import annotations

import datetime

import pytest

from app.broker.base import AbstractBroker, Message
from app.db.uow import UnitOfWork
from app.outbox.relay import OutboxRelay
from app.services.user import UserCreated

pytestmark = pytest.mark.asyncio


class _CollectingBroker(AbstractBroker):
    """Брокер-заглушка: копит опубликованное; может «падать» первые fail_first раз."""

    def __init__(self, fail_first: int = 0) -> None:
        self.published: list[Message] = []
        self._fail_first = fail_first

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...

    async def publish(self, message: Message) -> None:
        if self._fail_first > 0:
            self._fail_first -= 1
            raise RuntimeError("broker down")
        self.published.append(message)

    async def subscribe(self, topic, handler, *, group=None) -> None: ...


async def _enqueue(sessionmaker, event: UserCreated) -> None:
    async with UnitOfWork(sessionmaker) as uow:
        await uow.outbox.add_event(event)
        await uow.commit()


async def test_relay_publishes_and_marks(sessionmaker):
    await _enqueue(sessionmaker, UserCreated(id="u1", email="a@example.com"))
    await _enqueue(sessionmaker, UserCreated(id="u2", email="b@example.com"))

    broker = _CollectingBroker()
    relay = OutboxRelay(lambda: UnitOfWork(sessionmaker), broker)

    assert await relay.run_once() == 2
    assert {m.payload["id"] for m in broker.published} == {"u1", "u2"}

    # Повторный прогон — публиковать нечего (всё помечено published_at)
    assert await relay.run_once() == 0
    assert len(broker.published) == 2


async def test_relay_at_least_once_on_broker_failure(sessionmaker):
    await _enqueue(sessionmaker, UserCreated(id="u1", email="a@example.com"))

    broker = _CollectingBroker(fail_first=1)  # первый publish падает
    relay = OutboxRelay(lambda: UnitOfWork(sessionmaker), broker)

    # Брокер «лёг»: 0 отправлено, строка осталась неопубликованной
    assert await relay.run_once() == 0
    async with UnitOfWork(sessionmaker) as uow:
        assert len(await uow.outbox.fetch_unpublished(limit=10)) == 1

    # Брокер ожил: на следующем тике событие доставлено (at-least-once)
    assert await relay.run_once() == 1
    assert len(broker.published) == 1


async def test_cleanup_deletes_old_published(sessionmaker):
    await _enqueue(sessionmaker, UserCreated(id="u1", email="a@example.com"))
    relay = OutboxRelay(lambda: UnitOfWork(sessionmaker), _CollectingBroker())
    await relay.run_once()  # published_at = now

    # cutoff в будущем -> удалит всё опубликованное
    now = datetime.datetime.now(tz=datetime.UTC).replace(tzinfo=None)
    future = now + datetime.timedelta(days=1)
    async with UnitOfWork(sessionmaker) as uow:
        deleted = await uow.outbox.delete_published_before(future)
        await uow.commit()
    assert deleted == 1
