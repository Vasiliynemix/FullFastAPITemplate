"""
Реализация эквайринга поверх ОФИЦИАЛЬНОГО SDK YooKassa (`yookassa`).

У YooKassa есть свой SDK — поэтому используем ЕГО клиент, а не наш BaseHTTPClient
(см. правило в base.py). Наша задача — лишь правильно реализовать методы абстракции:
вызвать SDK и смаппить его ответ в доменные Payment/Refund.

SDK синхронный (под капотом requests), поэтому каждый вызов уносим в пул потоков через
anyio.to_thread — чтобы не блокировать event loop (тот же приём, что и argon2, ADR-0003).
Сам пакет `yookassa` импортируется ЛЕНИВО — он не нужен, если эквайринг через YooKassa
не используется (зависимость не тянется). raw = нативный объект SDK (RawT = Any).

Создание:
    acq = YooKassaAcquirer(shop_id="...", secret_key="...", default_return_url="https://app/return")
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

import anyio

from app.acquiring.base import (
    AbstractAcquirer,
    AcquiringError,
    Payment,
    PaymentStatus,
    Refund,
    WebhookEvent,
)
from app.core.logging import get_logger
from app.exceptions.base import NotFoundError

logger = get_logger("acquiring.yookassa")


class YooKassaAcquirer(AbstractAcquirer[Any]):
    def __init__(
        self,
        shop_id: str,
        secret_key: str,
        *,
        default_currency: str = "RUB",
        default_return_url: str = "",
    ) -> None:
        self._shop_id = shop_id
        self._secret_key = secret_key
        self._currency = default_currency
        self._return_url = default_return_url
        self._configured = False

    # ------------------------------------------------------------------
    # Жизненный цикл / конфигурация SDK
    # ------------------------------------------------------------------
    def _configure(self) -> None:
        # Ленивый импорт SDK: не тянем зависимость, если YooKassa не используется.
        from yookassa import Configuration

        Configuration.configure(self._shop_id, self._secret_key)
        self._configured = True
        logger.info("yookassa_configured", shop_id=self._shop_id)

    async def connect(self) -> None:
        self._configure()

    async def healthcheck(self) -> bool:
        # У YooKassa нет health-эндпоинта; настройки магазина — дешёвая проверка кред/связи.
        try:
            from yookassa import Settings

            if not self._configured:
                self._configure()
            await anyio.to_thread.run_sync(Settings.get_account_settings)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Платежи
    # ------------------------------------------------------------------
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
    ) -> Payment[Any]:
        from yookassa import Payment as YkPayment

        if not self._configured:
            self._configure()

        params: dict[str, Any] = {
            "amount": {"value": f"{amount:.2f}", "currency": currency or self._currency},
            "capture": capture,
            "confirmation": {"type": "redirect", "return_url": return_url or self._return_url},
        }
        if description:
            params["description"] = description
        if metadata:
            params["metadata"] = metadata

        key = idempotency_key or uuid.uuid4().hex
        raw = await self._call(YkPayment.create, params, key)
        return self._to_payment(raw)

    async def get_payment(self, payment_id: str) -> Payment[Any]:
        from yookassa import Payment as YkPayment

        if not self._configured:
            self._configure()
        raw = await self._call(YkPayment.find_one, payment_id)
        if raw is None:
            raise NotFoundError("Payment not found")
        return self._to_payment(raw)

    async def capture_payment(
        self,
        payment_id: str,
        *,
        amount: Decimal | None = None,
        idempotency_key: str | None = None,
    ) -> Payment[Any]:
        from yookassa import Payment as YkPayment

        if not self._configured:
            self._configure()
        # amount=None -> подтверждаем всю захолдированную сумму (пустой params)
        params: dict[str, Any] = {}
        if amount is not None:
            params["amount"] = {"value": f"{amount:.2f}", "currency": self._currency}
        key = idempotency_key or uuid.uuid4().hex
        raw = await self._call(YkPayment.capture, payment_id, params, key)
        return self._to_payment(raw)

    async def cancel_payment(
        self, payment_id: str, *, idempotency_key: str | None = None
    ) -> Payment[Any]:
        from yookassa import Payment as YkPayment

        if not self._configured:
            self._configure()
        key = idempotency_key or uuid.uuid4().hex
        raw = await self._call(YkPayment.cancel, payment_id, key)
        return self._to_payment(raw)

    async def create_refund(
        self,
        payment_id: str,
        *,
        amount: Decimal | None = None,
        idempotency_key: str | None = None,
    ) -> Refund[Any]:
        from yookassa import Refund as YkRefund

        if not self._configured:
            self._configure()
        if amount is None:
            # Полный возврат требует знать сумму платежа — берём её из платежа
            payment = await self.get_payment(payment_id)
            amount, currency = payment.amount, payment.currency
        else:
            currency = self._currency
        params = {
            "payment_id": payment_id,
            "amount": {"value": f"{amount:.2f}", "currency": currency},
        }
        key = idempotency_key or uuid.uuid4().hex
        raw = await self._call(YkRefund.create, params, key)
        return self._to_refund(raw)

    def parse_webhook(self, body: bytes) -> WebhookEvent[Any]:
        # YooKassa шлёт {"event": "...", "object": {...}}. Подпись не передаётся —
        # достоверность подтверждайте перепроверкой через get_payment (см. base.py).
        try:
            data = json.loads(body)
        except (ValueError, TypeError) as exc:
            raise AcquiringError("Invalid webhook body") from exc
        obj = data.get("object") or {}
        payment = self._payment_from_dict(obj) if obj else None
        return WebhookEvent(event=str(data.get("event", "")), payment=payment, raw=data)

    # ------------------------------------------------------------------
    # Внутреннее
    # ------------------------------------------------------------------
    @staticmethod
    async def _call(func: Any, *args: Any) -> Any:
        # Синхронный вызов SDK -> в пул потоков; ошибки SDK -> AcquiringError (502).
        try:
            return await anyio.to_thread.run_sync(func, *args)
        except Exception as exc:
            logger.warning("yookassa_call_failed", error=str(exc))
            raise AcquiringError(f"YooKassa request failed: {exc}") from exc

    @staticmethod
    def _to_payment(raw: Any) -> Payment[Any]:
        amount = getattr(raw, "amount", None)
        conf = getattr(raw, "confirmation", None)
        return Payment(
            id=raw.id,
            status=PaymentStatus(raw.status),
            amount=Decimal(str(amount.value)) if amount is not None else Decimal(0),
            currency=getattr(amount, "currency", "") if amount is not None else "",
            paid=bool(getattr(raw, "paid", False)),
            confirmation_url=getattr(conf, "confirmation_url", None) if conf is not None else None,
            description=getattr(raw, "description", None),
            metadata=dict(getattr(raw, "metadata", {}) or {}),
            raw=raw,
        )

    @staticmethod
    def _payment_from_dict(obj: dict[str, Any]) -> Payment[Any]:
        # Маппинг из СЫРОГО dict (вебхук приходит как JSON, а не как объект SDK).
        amount = obj.get("amount") or {}
        conf = obj.get("confirmation") or {}
        return Payment(
            id=str(obj.get("id", "")),
            status=PaymentStatus(obj.get("status", PaymentStatus.PENDING)),
            amount=Decimal(str(amount.get("value", "0"))),
            currency=str(amount.get("currency", "")),
            paid=bool(obj.get("paid", False)),
            confirmation_url=conf.get("confirmation_url"),
            description=obj.get("description"),
            metadata=dict(obj.get("metadata") or {}),
            raw=obj,
        )

    @staticmethod
    def _to_refund(raw: Any) -> Refund[Any]:
        amount = getattr(raw, "amount", None)
        return Refund(
            id=raw.id,
            payment_id=getattr(raw, "payment_id", ""),
            status=getattr(raw, "status", ""),
            amount=Decimal(str(amount.value)) if amount is not None else Decimal(0),
            currency=getattr(amount, "currency", "") if amount is not None else "",
            raw=raw,
        )
