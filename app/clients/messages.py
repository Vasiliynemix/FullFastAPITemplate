"""
Клиент API задач на отправку сообщений (MAX).

Авторизация: заголовок X-Api-Key (готовая обёртка ApiKeyHeaderHTTPClient).

Особенность: у внешнего API СВОЙ конверт — `{status, data}` на успехе и
`{status:false, error_code, message}` на ошибке. Клиент распаковывает его и приводит
к НАШЕМУ единому ApiResponse[T], сохраняя машинный `error_code` в `error.code`
(например TASK_NOT_FOUND / CANNOT_CANCEL / INVALID_PHONE).

Создание:
    client = MessagesClient(base_url="https://api.max.example", api_key="...")
"""

from __future__ import annotations

import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.clients.auth import ApiKeyHeaderHTTPClient
from app.clients.envelope import EnvelopeMixin
from app.clients.response import ApiError, ApiResponse
from app.core.config import settings


# ----------------------------------------------------------------------
# Модели
# ----------------------------------------------------------------------
class TaskState(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CreateMessageRequest(BaseModel):
    # Клиентская валидация — отсекаем явно битый ввод до сетевого вызова
    recipient_phone: str = Field(pattern=r"^\+[1-9]\d{1,14}$")  # E.164
    text: str = Field(min_length=1, max_length=4000)
    parse_mode: Literal["markdown"] | None = None


class CreateTaskResult(BaseModel):
    task_id: str


class TaskStatus(BaseModel):
    """Единая модель статуса под все state (поля по состояниям — опциональны)."""

    task_id: str
    state: TaskState
    delivered_at: datetime.datetime | None = None
    read_at: datetime.datetime | None = None
    # присутствуют только при state == failed
    error_code: str | None = None
    error_message: str | None = None

    @property
    def is_terminal(self) -> bool:
        # Состояние финальное — дальше уже не изменится (нет смысла опрашивать)
        return self.state != TaskState.PENDING


# ----------------------------------------------------------------------
# Клиент
# ----------------------------------------------------------------------
class MessagesClient(EnvelopeMixin, ApiKeyHeaderHTTPClient):
    service_name = "messages"
    api_key_header = "X-Api-Key"  # этот API ждёт ключ в заголовке X-Api-Key
    # Конверт этого API совпадает с дефолтом EnvelopeMixin
    # (status/data/error_code/message) — переопределять имена полей не нужно.

    async def create_task(
        self,
        recipient_phone: str,
        text: str,
        parse_mode: Literal["markdown"] | None = "markdown",
    ) -> ApiResponse[CreateTaskResult]:
        """Создать задачу на отправку. Возвращает task_id в data."""
        try:
            body = CreateMessageRequest(
                recipient_phone=recipient_phone, text=text, parse_mode=parse_mode
            )
        except ValidationError as exc:
            # Клиентская валидация не прошла — единый контракт, без сетевого вызова
            return ApiResponse(
                status=False,
                error=ApiError(code="INVALID_INPUT", message=str(exc), upstream_body=None),
            )
        return await self.call_envelope(
            "POST",
            "/api/messages",
            data_model=CreateTaskResult,
            json=body.model_dump(exclude_none=True, mode="json"),
        )

    async def get_status(self, task_id: str) -> ApiResponse[TaskStatus]:
        """Статус задачи. data.state -> pending/delivered/read/failed/cancelled."""
        return await self.call_envelope("GET", f"/api/messages/{task_id}", data_model=TaskStatus)

    async def cancel(self, task_id: str) -> ApiResponse[None]:
        """Отменить задачу (только в state=pending). data == null на успехе."""
        return await self.call_envelope("DELETE", f"/api/messages/{task_id}", data_model=None)


# ----------------------------------------------------------------------
# Синглтон-провайдер (один пул соединений на процесс)
# ----------------------------------------------------------------------
_client: MessagesClient | None = None


def get_messages_client() -> MessagesClient:
    """Ленивая инициализация. base_url/ключ — из настроек. Закрытие — в lifespan."""
    global _client
    if _client is None:
        _client = MessagesClient(
            base_url=settings.messages_api_base_url,
            api_key=settings.messages_api_key,
        )
    return _client


async def close_messages_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
