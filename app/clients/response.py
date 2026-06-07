"""
Единый контракт ответа HTTP-клиентов: ApiResponse[T].

Любой клиент через `call()` возвращает ЭТОТ конверт, а реальный результат внешнего
API лежит в `data` (уже провалидированный в модель T). Ошибки внешнего API не
бросаются, а кладутся в `error` со `status=false` — сервис ветвится по `status`.

Это аналог нашего внутреннего ServerResponse, но для ИСХОДЯЩИХ вызовов — намеренно
отдельный тип (внешний контракт не должен зависеть от внутреннего).
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiError(BaseModel):
    message: str
    code: str | None = None  # машинный код ошибки внешнего API (если есть)
    upstream_status: int | None = None  # HTTP-код внешнего API (если был ответ)
    upstream_body: Any | None = None  # тело ошибки внешнего API


class ApiResponse(BaseModel, Generic[T]):
    status: bool
    data: T | None = None
    error: ApiError | None = None

    @property
    def ok(self) -> bool:
        return self.status
