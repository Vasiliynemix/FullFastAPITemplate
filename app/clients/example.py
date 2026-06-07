"""
Примеры наследников — готовые обёртки + единый контракт ApiResponse[T].

Показывают рекомендуемый стиль: наследуем подготовленный класс под нужную авторизацию,
методы возвращают ApiResponse[Model] (через call()), результат — в data.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.clients.auth import (
    ApiKeyQueryHTTPClient,
    BearerHTTPClient,
    LoginTokenHTTPClient,
)
from app.clients.base import BaseHTTPClient
from app.clients.response import ApiResponse


# --- Модели ответов конкретного API ---
class Entry(BaseModel):
    name: str
    url: str


class Order(BaseModel):
    id: str
    status: str


class PublicAPIClient(BaseHTTPClient):
    """Без авторизации. call() -> единый конверт ApiResponse[list[Entry]]."""

    base_url = "https://api.publicapis.example"
    service_name = "publicapi"

    async def get_entries(self) -> ApiResponse[list[Entry]]:
        return await self.call("GET", "/entries", model=list[Entry])


class WeatherAPIClient(ApiKeyQueryHTTPClient):
    """Ключ в query — готовая обёртка ApiKeyQueryHTTPClient."""

    base_url = "https://api.weather.example"
    service_name = "weather"
    api_key_param = "appid"  # имя query-параметра под этот API

    async def forecast(self, city: str) -> ApiResponse[dict[str, Any]]:
        return await self.call("GET", "/forecast", params={"q": city}, model=dict[str, Any])


class PartnerAPIClient(BearerHTTPClient):
    """Bearer-токен (готовая обёртка). Создаётся как PartnerAPIClient(token=...)."""

    base_url = "https://partner.example/api/v1"
    service_name = "partner"

    async def create_order(self, payload: dict[str, Any]) -> ApiResponse[Order]:
        return await self.call("POST", "/orders", json=payload, model=Order)


class BillingAPIClient(LoginTokenHTTPClient):
    """login/pass -> token. Создаётся как BillingAPIClient(username=..., password=...)."""

    base_url = "https://billing.example/api"
    service_name = "billing"
    login_path = "/login"  # эндпоинт логина этого API

    async def get_invoice(self, invoice_id: str) -> ApiResponse[dict[str, Any]]:
        return await self.call("GET", f"/invoices/{invoice_id}", model=dict[str, Any])
