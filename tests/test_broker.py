"""Семантика групп брокера: fan-out (разные группы) vs competing consumers (одна группа)."""

from __future__ import annotations

import pytest

from app.broker.base import Message
from app.broker.memory import InMemoryBroker

pytestmark = pytest.mark.asyncio


async def test_different_groups_fanout():
    broker = InMemoryBroker()
    a: list[int] = []
    b: list[int] = []

    async def ha(m: Message) -> None:
        a.append(m.payload["n"])

    async def hb(m: Message) -> None:
        b.append(m.payload["n"])

    # Разные группы -> каждый получает КАЖДОЕ сообщение
    await broker.subscribe("t", ha, group="g1")
    await broker.subscribe("t", hb, group="g2")
    for n in range(3):
        await broker.publish(Message(topic="t", payload={"n": n}))
    await broker.disconnect()

    assert a == [0, 1, 2]
    assert b == [0, 1, 2]


async def test_same_group_competing_consumers():
    broker = InMemoryBroker()
    seen: list[tuple[str, int]] = []

    async def w1(m: Message) -> None:
        seen.append(("w1", m.payload["n"]))

    async def w2(m: Message) -> None:
        seen.append(("w2", m.payload["n"]))

    # Одна группа -> сообщения ДЕЛЯТСЯ (round-robin), не дублируются
    await broker.subscribe("t", w1, group="shared")
    await broker.subscribe("t", w2, group="shared")
    for n in range(4):
        await broker.publish(Message(topic="t", payload={"n": n}))
    await broker.disconnect()

    # каждое сообщение обработано РОВНО один раз
    assert len(seen) == 4
    assert sorted(n for _, n in seen) == [0, 1, 2, 3]
    # и распределилось между двумя воркерами (round-robin)
    assert {w for w, _ in seen} == {"w1", "w2"}


async def test_default_group_is_per_handler_fanout():
    # Без явного group разные функции -> разные группы по умолчанию -> fan-out
    broker = InMemoryBroker()
    a: list[int] = []
    b: list[int] = []

    async def ha(m: Message) -> None:
        a.append(m.payload["n"])

    async def hb(m: Message) -> None:
        b.append(m.payload["n"])

    await broker.subscribe("t", ha)
    await broker.subscribe("t", hb)
    await broker.publish(Message(topic="t", payload={"n": 1}))
    await broker.disconnect()

    assert a == [1] and b == [1]
