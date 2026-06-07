"""
Очередь исходящих уведомлений через брокер — типизированное событие + консьюмер.

* NotificationRequested — СОБЫТИЕ (что кладём в очередь). Это и контракт payload,
  и валидация, и одновременно тело HTTP-запроса (используется в роуте).
* handle_notification — КОНСЬЮМЕР: получает уже распарсенное событие (а не сырой dict)
  и отправляет уведомление через внешний API ВНЕ request lifecycle.

Продюсер публикует событие через EventBus (см. app/broker/events.py):
    await bus.publish(NotificationRequested(recipient_phone=..., text=...))
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field

from app.broker.events import Event
from app.clients.messages import get_messages_client
from app.core.logging import get_logger
from app.services.notification import NotificationService

logger = get_logger("consumer.notifications")


class NotificationRequested(Event):
    """Запрос на отправку уведомления (payload очереди + тело HTTP-запроса)."""

    topic: ClassVar[str] = "notifications.send"

    recipient_phone: str = Field(pattern=r"^\+[1-9]\d{1,14}$")  # E.164
    text: str = Field(min_length=1, max_length=4000)
    markdown: bool = False


async def handle_notification(event: NotificationRequested) -> None:
    """Консьюмер: отправляет уведомление через внешний API."""
    service = NotificationService(get_messages_client())
    try:
        task = await service.send(event.recipient_phone, event.text, markdown=event.markdown)
        logger.info("notification_sent", task_id=task.task_id)
    except Exception:
        # Консьюмер НЕ должен падать — логируем; здесь подключается ретрай/DLQ
        logger.error("notification_failed", phone=event.recipient_phone, exc_info=True)
