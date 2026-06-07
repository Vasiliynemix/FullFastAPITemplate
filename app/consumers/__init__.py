"""
Регистрация консьюмеров брокера.

register_consumers() вызывается в lifespan на старте (после broker.connect()):
здесь все подписки в одном месте. Добавляете обработчик — добавляете строку subscribe.
"""

from __future__ import annotations

from app.broker.events import EventBus
from app.consumers.notifications import NotificationRequested, handle_notification
from app.consumers.users import handle_user_created_audit, handle_user_created_welcome
from app.services.user import UserCreated


async def register_consumers(bus: EventBus) -> None:
    """Все подписки брокера в одном месте. Вызывается в lifespan на старте."""
    # Типизированная подписка: handler получает NotificationRequested, а не сырой Message.
    # group="notifications" -> при нескольких репликах они ДЕЛЯТ очередь (не дублируют отправку).
    await bus.subscribe(NotificationRequested, handle_notification, group="notifications")

    # Fan-out: ОДНО событие UserCreated -> НЕСКОЛЬКО независимых обработчиков.
    # Разные group -> каждый получает КАЖДОЕ событие (на Kafka/RabbitMQ тоже корректно).
    await bus.subscribe(UserCreated, handle_user_created_audit, group="users-audit")
    await bus.subscribe(UserCreated, handle_user_created_welcome, group="users-welcome")
