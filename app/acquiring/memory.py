"""
In-memory заглушка эквайринга — для тестов и локальной разработки (без сети и кредов).

Дефолт фабрики (как InMemoryBroker у брокера): даёт пройти весь сценарий
create -> get -> capture/cancel -> refund без реального провайдера. RawT = dict —
демонстрирует кейс «провайдера без SDK» (raw = сырой dict). НЕ для прода.

Симуляция упрощена: confirmation_url ведёт в никуда, переходы статусов делаются явными
вызовами (capture -> succeeded, cancel -> canceled).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from app.acquiring.base import (
    AbstractAcquirer,
    Payment,
    PaymentStatus,
    Refund,
    WebhookEvent,
)
from app.exceptions.base import ConflictError, NotFoundError


class InMemoryAcquirer(AbstractAcquirer[dict[str, Any]]):
    def __init__(self, *, default_currency: str = "RUB") -> None:
        self._currency = default_currency
        self._payments: dict[str, Payment[dict[str, Any]]] = {}

    async def create_payment(
        self,
        amount: Decimal,
        *,
        currency: str | None = None,
        description: str | None = None,
        return_url: str | None = None,
        capture: bool = True,
        metadata: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> Payment[dict[str, Any]]:
        pid = uuid.uuid4().hex
        # capture=True -> ждём подтверждения оплаты пользователем (pending);
        # capture=False -> двухстадийный: попадёт в waiting_for_capture после оплаты.
        payment = Payment(
            id=pid,
            status=PaymentStatus.PENDING,
            amount=amount,
            currency=currency or self._currency,
            paid=False,
            confirmation_url=f"https://stub.acquirer/pay/{pid}",
            description=description,
            metadata=metadata or {},
            raw={"id": pid, "simulated": True},
        )
        self._payments[pid] = payment
        return payment

    async def get_payment(self, payment_id: str) -> Payment[dict[str, Any]]:
        payment = self._payments.get(payment_id)
        if payment is None:
            raise NotFoundError("Payment not found")
        return payment

    async def capture_payment(
        self,
        payment_id: str,
        *,
        amount: Decimal | None = None,
        idempotency_key: str | None = None,
    ) -> Payment[dict[str, Any]]:
        payment = await self.get_payment(payment_id)
        payment.status = PaymentStatus.SUCCEEDED
        payment.paid = True
        return payment

    async def cancel_payment(
        self, payment_id: str, *, idempotency_key: str | None = None
    ) -> Payment[dict[str, Any]]:
        payment = await self.get_payment(payment_id)
        payment.status = PaymentStatus.CANCELED
        return payment

    async def create_refund(
        self,
        payment_id: str,
        *,
        amount: Decimal | None = None,
        idempotency_key: str | None = None,
    ) -> Refund[dict[str, Any]]:
        payment = await self.get_payment(payment_id)
        if not payment.is_succeeded:
            raise ConflictError("Only succeeded payments can be refunded")
        refund_amount = amount if amount is not None else payment.amount
        rid = uuid.uuid4().hex
        return Refund(
            id=rid,
            payment_id=payment_id,
            status="succeeded",
            amount=refund_amount,
            currency=payment.currency,
            raw={"id": rid, "payment_id": payment_id, "simulated": True},
        )

    def parse_webhook(self, body: bytes) -> WebhookEvent[dict[str, Any]]:
        import json

        data = json.loads(body)
        return WebhookEvent(event=str(data.get("event", "")), payment=None, raw=data)
