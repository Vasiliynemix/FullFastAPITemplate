"""
Абстракция брокера сообщений (фоновая обработка / события).

Сервисы публикуют события через AbstractBroker, не зная о Kafka/RabbitMQ.
Это даёт горизонтальное масштабирование: тяжёлую работу выносим из request lifecycle.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Message:
    topic: str
    payload: dict[str, Any]
    key: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


# Тип обработчика-подписчика
Handler = Callable[[Message], Awaitable[None]]


def default_group(handler: Callable[..., Awaitable[Any]]) -> str:
    """
    Группа по умолчанию — стабильный id из самой функции-обработчика.
    Разные обработчики -> разные группы -> fan-out (каждый получает всё). Стабильно
    между рестартами (важно для Kafka offsets / RabbitMQ очередей).

    Принимает любой async-обработчик (Handler или типизированный EventHandler[E]) —
    использует только __module__/__qualname__.
    """
    return f"{handler.__module__}.{handler.__qualname__}"


class AbstractBroker(ABC):
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def publish(self, message: Message) -> None: ...

    @abstractmethod
    async def subscribe(self, topic: str, handler: Handler, *, group: str | None = None) -> None:
        """
        Подписать обработчик на топик.

        group — идентификатор группы потребителей (семантика как у Kafka consumer group):
          * РАЗНЫЕ group  -> каждый получает КАЖДОЕ сообщение (fan-out);
          * ОДИНАКОВЫЙ group -> делят нагрузку (competing consumers, балансировка);
          * None -> группа по умолчанию из функции-обработчика (=> fan-out, стабильно).
        """
