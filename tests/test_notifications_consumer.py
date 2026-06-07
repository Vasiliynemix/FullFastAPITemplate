"""Типизированная очередь уведомлений: EventBus (publish/subscribe) + консьюмер."""

from __future__ import annotations

import json

import httpx
import pytest

from app.broker.base import Message
from app.broker.events import EventBus
from app.broker.memory import InMemoryBroker
from app.clients.messages import MessagesClient
from app.consumers import register_consumers
from app.consumers.notifications import NotificationRequested, handle_notification

pytestmark = pytest.mark.asyncio


async def test_publish_serializes_event_to_topic():
    broker = InMemoryBroker()
    received: list[Message] = []

    async def spy(msg: Message) -> None:
        received.append(msg)

    await broker.subscribe(NotificationRequested.topic, spy)
    bus = EventBus(broker)
    await bus.publish(
        NotificationRequested(recipient_phone="+79991234567", text="hi", markdown=True)
    )
    await broker.disconnect()

    assert len(received) == 1
    assert received[0].topic == "notifications.send"
    assert received[0].payload == {
        "recipient_phone": "+79991234567",
        "text": "hi",
        "markdown": True,
    }


async def test_subscribe_delivers_typed_event():
    broker = InMemoryBroker()
    got: list[NotificationRequested] = []

    async def handler(event: NotificationRequested) -> None:  # типизированный объект!
        got.append(event)

    bus = EventBus(broker)
    await bus.subscribe(NotificationRequested, handler)
    await bus.publish(NotificationRequested(recipient_phone="+79991234567", text="hi"))
    await broker.disconnect()

    assert len(got) == 1
    assert isinstance(got[0], NotificationRequested)
    assert got[0].recipient_phone == "+79991234567"


async def test_invalid_payload_does_not_crash_consumer():
    broker = InMemoryBroker()
    called = {"n": 0}

    async def handler(event: NotificationRequested) -> None:
        called["n"] += 1

    bus = EventBus(broker)
    await bus.subscribe(NotificationRequested, handler)
    # payload без обязательных полей -> ValidationError внутри обёртки, handler не зовётся
    await broker.publish(Message(topic=NotificationRequested.topic, payload={"text": "x"}))
    await broker.disconnect()

    assert called["n"] == 0


async def test_consumer_sends_via_messages_client(monkeypatch):
    seen: dict = {}

    def http_handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        seen["key"] = req.headers.get("x-api-key")
        return httpx.Response(200, json={"status": True, "data": {"task_id": "T1"}})

    client = MessagesClient(
        base_url="https://api.test", api_key="K", transport=httpx.MockTransport(http_handler)
    )
    monkeypatch.setattr("app.consumers.notifications.get_messages_client", lambda: client)

    await handle_notification(NotificationRequested(recipient_phone="+79991234567", text="hi"))

    assert seen["body"]["recipient_phone"] == "+79991234567"
    assert seen["key"] == "K"


async def test_full_flow_publish_then_consume(monkeypatch):
    calls = {"n": 0}

    def http_handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"status": True, "data": {"task_id": "T1"}})

    client = MessagesClient(
        base_url="https://api.test", api_key="K", transport=httpx.MockTransport(http_handler)
    )
    monkeypatch.setattr("app.consumers.notifications.get_messages_client", lambda: client)

    broker = InMemoryBroker()
    bus = EventBus(broker)
    await register_consumers(bus)  # типизированная подписка
    await bus.publish(NotificationRequested(recipient_phone="+79991234567", text="hi"))
    await broker.disconnect()

    assert calls["n"] == 1
