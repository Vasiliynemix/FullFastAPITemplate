"""
Ручка постановки уведомления в очередь (продюсер через EventBus).

Тело запроса — само событие NotificationRequested (его поля = контракт payload).
Роут НЕ ждёт внешний API: публикует событие и сразу отвечает 202. Реальную отправку
делает консьюмер (app/consumers/notifications.py) вне запроса.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import EventBusDep
from app.consumers.notifications import NotificationRequested
from app.schemas.response import SuccessResponse, success

router = APIRouter()


@router.post("", response_model=SuccessResponse[dict], status_code=status.HTTP_202_ACCEPTED)
async def queue_notification(
    event: NotificationRequested, bus: EventBusDep
) -> SuccessResponse[dict]:
    # Типобезопасно: event уже провалидирован, bus.publish сам возьмёт topic+payload
    await bus.publish(event)
    return success({"queued": True})
