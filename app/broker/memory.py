"""
In-memory брокер (mock).

Используется по умолчанию (BROKER_TYPE=memory) для локальной разработки и тестов.
Доставляет сообщения подписчикам в рамках процесса через asyncio. Не персистентный.

Поддерживает группы потребителей (как Kafka): подписчики хранятся как
topic -> group -> [handlers]. На publish сообщение уходит в КАЖДУЮ группу (fan-out),
а ВНУТРИ группы — одному обработчику по кругу (round-robin = competing consumers).
Полноценные Kafka/RabbitMQ реализации подключаются через factory.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

from app.broker.base import AbstractBroker, Handler, Message, default_group
from app.core.logging import get_logger

logger = get_logger("broker.memory")


class InMemoryBroker(AbstractBroker):
    def __init__(self) -> None:
        # topic -> group -> список обработчиков
        self._groups: dict[str, dict[str, list[Handler]]] = defaultdict(lambda: defaultdict(list))
        self._rr: dict[tuple[str, str], int] = {}  # счётчик round-robin на (topic, group)
        self._tasks: set[asyncio.Task] = set()

    async def connect(self) -> None:
        logger.info("broker_connected", backend="memory")

    async def disconnect(self) -> None:
        # Дожидаемся незавершённых доставок (graceful)
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("broker_disconnected", backend="memory")

    async def publish(self, message: Message) -> None:
        for group, handlers in self._groups.get(message.topic, {}).items():
            if not handlers:
                continue
            # competing consumers внутри группы: round-robin к одному обработчику
            key = (message.topic, group)
            idx = self._rr.get(key, 0) % len(handlers)
            self._rr[key] = idx + 1
            handler = handlers[idx]
            # Не блокируем publisher — доставка в фоне
            task = asyncio.create_task(self._safe_dispatch(handler, message))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def subscribe(self, topic: str, handler: Handler, *, group: str | None = None) -> None:
        g = group or default_group(handler)
        self._groups[topic][g].append(handler)
        logger.info("broker_subscribed", topic=topic, group=g)

    async def _safe_dispatch(self, handler: Handler, message: Message) -> None:
        try:
            await handler(message)
        except Exception:
            logger.error("broker_handler_failed", topic=message.topic, exc_info=True)
