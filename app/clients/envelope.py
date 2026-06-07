"""
EnvelopeMixin — переиспользуемая распаковка «своего» конверта внешнего API.

Многие API оборачивают ответ в собственный конверт: на успехе {status, data},
на ошибке {status:false, error_code, message}. Логика разворачивания такого конверта
в наш единый ApiResponse[T] всегда одна — отличаются лишь ИМЕНА полей. Поэтому она
здесь, а не в каждом клиенте: подмешайте EnvelopeMixin и зовите `call_envelope()`.

Под нестандартный конверт:
* поменяйте имена полей (envelope_*_field), ИЛИ
* переопределите хуки `_is_success` / `_extract_data` / `_extract_error`.

Комбинируется с любой auth-обёрткой:
    class MessagesClient(EnvelopeMixin, ApiKeyHeaderHTTPClient): ...
"""

from __future__ import annotations

from typing import Any, TypeVar, overload

from pydantic import BaseModel, ValidationError

from app.clients.base import BaseHTTPClient, ExternalAPIError
from app.clients.response import ApiError, ApiResponse

M = TypeVar("M", bound=BaseModel)


class EnvelopeMixin:
    # Имена полей внешнего конверта (дефолт — частая схема). Переопределяемы.
    envelope_status_field: str = "status"
    envelope_data_field: str = "data"
    envelope_error_code_field: str = "error_code"
    envelope_error_message_field: str = "message"

    # --- Хуки под нестандартные конверты ---
    def _is_success(self, raw: dict[str, Any]) -> bool:
        return bool(raw.get(self.envelope_status_field, False))

    def _extract_data(self, raw: dict[str, Any]) -> Any:
        return raw.get(self.envelope_data_field)

    def _extract_error(self, raw: dict[str, Any]) -> tuple[str | None, str | None]:
        return (
            raw.get(self.envelope_error_code_field),
            raw.get(self.envelope_error_message_field),
        )

    # ------------------------------------------------------------------
    @overload
    async def call_envelope(
        self, method: str, url: str, *, data_model: type[M], **kw: Any
    ) -> ApiResponse[M]: ...
    @overload
    async def call_envelope(
        self, method: str, url: str, *, data_model: None = ..., **kw: Any
    ) -> ApiResponse[None]: ...
    async def call_envelope(
        self, method: str, url: str, *, data_model: Any = None, **kw: Any
    ) -> ApiResponse[Any]:
        """
        Запрос -> распаковка внешнего конверта -> наш ApiResponse[T].
        Ошибки (HTTP и прикладные) не бросаются, а кладутся в error со status=false.
        """
        try:
            raw = await self.request(method, url, **kw)  # type: ignore[attr-defined]
        except ExternalAPIError as exc:
            # HTTP-ошибка (401/5xx). Если тело несёт код ошибки — достаём его.
            body = exc.upstream_body
            code = body.get(self.envelope_error_code_field) if isinstance(body, dict) else None
            msg = body.get(self.envelope_error_message_field) if isinstance(body, dict) else None
            return ApiResponse(
                status=False,
                error=ApiError(
                    code=code,
                    message=msg or exc.message,
                    upstream_status=exc.upstream_status,
                    upstream_body=body,
                ),
            )

        if not isinstance(raw, dict):
            return ApiResponse(
                status=False,
                error=ApiError(message="unexpected response shape", upstream_body=raw),
            )

        if not self._is_success(raw):
            code, msg = self._extract_error(raw)
            return ApiResponse(
                status=False,
                error=ApiError(code=code, message=msg or "external error", upstream_body=raw),
            )

        data = self._extract_data(raw)
        if data_model is not None and data is not None:
            try:
                data = data_model.model_validate(data)
            except ValidationError:
                return ApiResponse(
                    status=False,
                    error=ApiError(message="response schema mismatch", upstream_body=data),
                )
        return ApiResponse(status=True, data=data)


class EnvelopeHTTPClient(EnvelopeMixin, BaseHTTPClient):
    """Конверт + база без авторизации. С авторизацией: (EnvelopeMixin, BearerHTTPClient) и т.п."""
