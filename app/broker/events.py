"""
Типизированные события поверх брокера (по аналогии с HTTP-клиентом: генерики + модели).

Идея: событие — это Pydantic-модель, привязанная к топику (ClassVar `topic`).
EventBus даёт типобезопасные publish/subscribe:
* продюсер ТОЧНО знает, что класть в payload — это поля модели события;
* консьюмер получает уже РАСПАРСЕННЫЙ и провалидированный объект события, а не сырой dict.

Так уходит «магия строковых ключей» в payload — контракт события в одном месте.

Использование:
    class NotificationRequested(Event):
        topic = "notifications.send"
        recipient_phone: str
        text: str

    bus = EventBus(broker)
    await bus.publish(NotificationRequested(recipient_phone="+7...", text="hi"))   # продюсер
    await bus.subscribe(NotificationRequested, handler)                            # консьюмер
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import ClassVar, TypeVar

from pydantic import BaseModel, ValidationError

from app.broker.base import AbstractBroker, Message, default_group
from app.core.logging import get_logger

logger = get_logger("eventbus")


class Event(BaseModel):
    """Базовое событие. Наследник задаёт `topic` и свои поля (payload)."""

    topic: ClassVar[str]


E = TypeVar("E", bound=Event)
EventHandler = Callable[[E], Awaitable[None]]


class EventBus:
    """Типизированная обёртка над AbstractBroker."""

    def __init__(self, broker: AbstractBroker) -> None:
        self._broker = broker

    async def publish(self, event: Event) -> None:
        """Опубликовать событие. Топик и payload берутся из самого события."""
        await self._broker.publish(
            Message(topic=event.topic, payload=event.model_dump(mode="json"))
        )

    async def subscribe(
        self, event_type: type[E], handler: EventHandler[E], *, group: str | None = None
    ) -> None:
        """
        Подписать обработчик на тип события. handler получает ТИПИЗИРОВАННЫЙ объект
        event_type, а не сырой Message — payload парсится/валидируется автоматически.

        group — группа потребителей (см. AbstractBroker.subscribe). Разные группы ->
        fan-out, одинаковая -> деление нагрузки. По умолчанию группа берётся из РЕАЛЬНОГО
        обработчика (а не из внутренней обёртки) -> разные handler'ы => fan-out.
        """
        g = group or default_group(handler)

        async def _wrapped(message: Message) -> None:
            try:
                event = event_type.model_validate(message.payload)
            except ValidationError:
                # Битый payload не должен ронять консьюмер — логируем и пропускаем
                logger.error("event_validation_failed", topic=event_type.topic, exc_info=True)
                return
            await handler(event)

        await self._broker.subscribe(event_type.topic, _wrapped, group=g)
