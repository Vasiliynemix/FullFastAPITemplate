"""
RabbitMQ-реализация брокера (aio-pika).

Включается через BROKER_TYPE=rabbitmq, BROKER_URL=amqp://guest:guest@host/.
Robust-соединение (авто-переподключение).

Fan-out через fanout-exchange на каждый топик + ОТДЕЛЬНАЯ очередь на каждую группу,
привязанная к этому exchange. Так каждая группа получает КАЖДОЕ сообщение, а несколько
консьюмеров в ОДНОЙ группе (одна очередь) делят нагрузку (competing consumers).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import orjson

from app.broker.base import AbstractBroker, Handler, Message, default_group
from app.core.logging import get_logger

if TYPE_CHECKING:
    from aio_pika.abc import AbstractChannel, AbstractRobustConnection

logger = get_logger("broker.rabbitmq")


class RabbitMQBroker(AbstractBroker):
    def __init__(self, url: str) -> None:
        self._url = url
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractChannel | None = None

    async def connect(self) -> None:
        import aio_pika

        self._connection = await aio_pika.connect_robust(self._url)
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=100)  # параллелизм потребления
        logger.info("broker_connected", backend="rabbitmq")

    async def disconnect(self) -> None:
        if self._connection is not None:
            await self._connection.close()
        logger.info("broker_disconnected", backend="rabbitmq")

    async def publish(self, message: Message) -> None:
        import aio_pika

        assert self._channel is not None, "Broker not connected"
        # Публикуем в fanout-exchange топика — он разошлёт во все привязанные очереди (группы)
        exchange = await self._channel.declare_exchange(
            message.topic, aio_pika.ExchangeType.FANOUT, durable=True
        )
        await exchange.publish(
            aio_pika.Message(
                body=orjson.dumps(message.payload),
                content_type="application/json",
            ),
            routing_key="",  # fanout игнорирует routing key
        )

    async def subscribe(self, topic: str, handler: Handler, *, group: str | None = None) -> None:
        import aio_pika

        assert self._channel is not None, "Broker not connected"
        g = group or default_group(handler)
        # Своя очередь на группу, привязанная к fanout-exchange топика -> группа получает всё
        exchange = await self._channel.declare_exchange(
            topic, aio_pika.ExchangeType.FANOUT, durable=True
        )
        queue = await self._channel.declare_queue(f"{topic}.{g}", durable=True)
        await queue.bind(exchange)

        async def _on_message(raw) -> None:  # type: ignore[no-untyped-def]
            async with raw.process():
                msg = Message(topic=topic, payload=orjson.loads(raw.body))
                try:
                    await handler(msg)
                except Exception:
                    logger.error("broker_handler_failed", topic=topic, exc_info=True)

        await queue.consume(_on_message)
        logger.info("broker_subscribed", topic=topic, group=g)
