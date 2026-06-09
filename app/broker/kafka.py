"""
Kafka-реализация брокера (aiokafka).

Включается через BROKER_TYPE=kafka, BROKER_URL=host:9092. Producer/consumer
создаются на старте. payload сериализуется orjson. Для consumer'а используется
отдельная фоновая задача на каждый подписанный топик.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import orjson

from app.broker.base import AbstractBroker, Handler, Message, default_group
from app.core.config import settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    from aiokafka import AIOKafkaProducer

logger = get_logger("broker.kafka")


class KafkaBroker(AbstractBroker):
    def __init__(self, bootstrap_servers: str) -> None:
        self._servers = bootstrap_servers
        self._producer: AIOKafkaProducer | None = None
        self._consumer_tasks: set[asyncio.Task] = set()

    async def connect(self) -> None:
        from aiokafka import AIOKafkaProducer

        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._servers,
            value_serializer=lambda v: orjson.dumps(v),
            linger_ms=5,  # микробатчинг — выше throughput при высокой нагрузке
            acks=1,
        )
        await self._producer.start()
        logger.info("broker_connected", backend="kafka", servers=self._servers)

    async def disconnect(self) -> None:
        for task in self._consumer_tasks:
            task.cancel()
        if self._producer is not None:
            await self._producer.stop()
        logger.info("broker_disconnected", backend="kafka")

    async def healthcheck(self) -> bool:
        return self._producer is not None

    async def publish(self, message: Message) -> None:
        assert self._producer is not None, "Broker not connected"
        key = message.key.encode() if message.key else None
        await self._producer.send_and_wait(message.topic, message.payload, key=key)

    async def subscribe(self, topic: str, handler: Handler, *, group: str | None = None) -> None:
        g = group or default_group(handler)
        task = asyncio.create_task(self._consume(topic, handler, g))
        self._consumer_tasks.add(task)
        task.add_done_callback(self._consumer_tasks.discard)

    async def _consume(self, topic: str, handler: Handler, group: str) -> None:
        from aiokafka import AIOKafkaConsumer

        # group_id из group: РАЗНЫЕ группы -> каждая получает все сообщения (fan-out);
        # ОДИНАКОВАЯ group у нескольких консьюмеров -> Kafka балансирует партиции (деление).
        consumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=self._servers,
            value_deserializer=lambda v: orjson.loads(v),
            group_id=f"{settings.app_name}.{group}",
            enable_auto_commit=True,
        )
        await consumer.start()
        logger.info("broker_subscribed", topic=topic, group=group)
        try:
            async for record in consumer:
                msg = Message(topic=topic, payload=record.value)
                try:
                    await handler(msg)
                except Exception:
                    logger.error("broker_handler_failed", topic=topic, exc_info=True)
        finally:
            await consumer.stop()
