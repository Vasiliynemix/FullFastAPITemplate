"""
Сервис уведомлений — бизнес-логика поверх MessagesClient.

Здесь живёт ДОМЕННАЯ логика: что и когда отправлять, как трактовать ошибки внешнего
API. Сам HTTP — забота клиента (app/clients). Наружу сервис, как и все остальные,
бросает только ServerException — глобальный обработчик отдаст единый ErrorResponse.

Маппинг: машинные коды внешнего API -> наши HTTP-статусы/коды.
"""

from __future__ import annotations

from app.clients.messages import CreateTaskResult, MessagesClient, TaskStatus
from app.clients.response import ApiError
from app.decorators.logging import logged
from app.exceptions.base import ServerException
from app.schemas.response import ErrorCode

# Коды ошибок внешнего API -> (наш HTTP-статус, наш ErrorCode)
_ERROR_MAP: dict[str, tuple[int, ErrorCode]] = {
    "INVALID_INPUT": (400, ErrorCode.BAD_REQUEST),
    "INVALID_PHONE": (400, ErrorCode.BAD_REQUEST),
    "EMPTY_TEXT": (400, ErrorCode.BAD_REQUEST),
    "TEXT_TOO_LONG": (400, ErrorCode.BAD_REQUEST),
    "INVALID_MARKDOWN": (400, ErrorCode.BAD_REQUEST),
    "TASK_NOT_FOUND": (404, ErrorCode.NOT_FOUND),
    "CANNOT_CANCEL": (409, ErrorCode.CONFLICT),
}


def _raise(error: ApiError | None) -> ServerException:
    """Конвертирует ошибку клиента в наш ServerException по коду внешнего API."""
    code = error.code if error else None
    message = error.message if error else "messages API error"
    status, our_code = _ERROR_MAP.get(code or "", (502, ErrorCode.UNAVAILABLE))
    return ServerException(status_code=status, message=message, code=our_code)


class NotificationService:
    def __init__(self, messages: MessagesClient) -> None:
        self.messages = messages

    @logged("notification.send")
    async def send(self, phone: str, text: str, *, markdown: bool = False) -> CreateTaskResult:
        res = await self.messages.create_task(
            phone, text, parse_mode="markdown" if markdown else None
        )
        if not res.status or res.data is None:
            raise _raise(res.error)
        return res.data

    @logged("notification.status")
    async def status(self, task_id: str) -> TaskStatus:
        res = await self.messages.get_status(task_id)
        if not res.status or res.data is None:
            raise _raise(res.error)
        return res.data

    @logged("notification.cancel")
    async def cancel(self, task_id: str) -> None:
        res = await self.messages.cancel(task_id)
        if not res.status:
            raise _raise(res.error)
