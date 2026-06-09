"""
Эквайринг: сценарий через InMemoryAcquirer, маппинг YooKassa и фабрика.

InMemoryAcquirer гоняет весь флоу без сети. YooKassa-маппинг проверяем на фейковом
объекте SDK (SimpleNamespace) и на dict вебхука — без реальных вызовов и без пакета SDK.
"""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.acquiring.base import PaymentStatus
from app.acquiring.factory import get_acquirer, get_acquirers, reset_acquirers
from app.acquiring.memory import InMemoryAcquirer
from app.acquiring.yookassa import YooKassaAcquirer
from app.core.config import AcquirerName, Settings, settings
from app.exceptions.base import ConflictError, NotFoundError
from app.schemas.account import AmountRequest


# ---------------- InMemoryAcquirer (полный флоу) ----------------
async def test_memory_create_capture_refund_flow():
    acq = InMemoryAcquirer()
    p = await acq.create_payment(Decimal("100.00"), description="order #1")
    assert p.status is PaymentStatus.PENDING
    assert p.paid is False
    assert p.confirmation_url and p.id

    # get возвращает тот же платёж
    assert (await acq.get_payment(p.id)).id == p.id

    # capture -> succeeded + paid
    captured = await acq.capture_payment(p.id)
    assert captured.status is PaymentStatus.SUCCEEDED
    assert captured.paid is True

    # refund оплаченного -> succeeded
    refund = await acq.create_refund(p.id, amount=Decimal("40.00"))
    assert refund.payment_id == p.id
    assert refund.amount == Decimal("40.00")


async def test_memory_cancel():
    acq = InMemoryAcquirer()
    p = await acq.create_payment(Decimal("10.00"))
    canceled = await acq.cancel_payment(p.id)
    assert canceled.status is PaymentStatus.CANCELED


async def test_memory_refund_requires_succeeded():
    acq = InMemoryAcquirer()
    p = await acq.create_payment(Decimal("10.00"))  # pending, не оплачен
    with pytest.raises(ConflictError):
        await acq.create_refund(p.id)


async def test_memory_get_unknown_raises_404():
    with pytest.raises(NotFoundError):
        await InMemoryAcquirer().get_payment("nope")


# ---------------- YooKassa: маппинг без сети ----------------
def test_yookassa_maps_sdk_object_to_domain():
    raw = SimpleNamespace(
        id="pay_1",
        status="succeeded",
        paid=True,
        amount=SimpleNamespace(value="100.00", currency="RUB"),
        confirmation=SimpleNamespace(confirmation_url="https://yk/redirect"),
        description="order",
        metadata={"order_id": "42"},
    )
    p = YooKassaAcquirer._to_payment(raw)
    assert p.id == "pay_1"
    assert p.status is PaymentStatus.SUCCEEDED
    assert p.amount == Decimal("100.00")
    assert p.currency == "RUB"
    assert p.paid is True
    assert p.confirmation_url == "https://yk/redirect"
    assert p.metadata == {"order_id": "42"}
    assert p.raw is raw  # нативный объект SDK доступен через raw


def test_yookassa_parse_webhook():
    acq = YooKassaAcquirer(shop_id="s", secret_key="k")  # без сети, SDK не импортируется
    body = json.dumps(
        {
            "event": "payment.succeeded",
            "object": {
                "id": "pay_1",
                "status": "succeeded",
                "paid": True,
                "amount": {"value": "100.00", "currency": "RUB"},
            },
        }
    ).encode()
    ev = acq.parse_webhook(body)
    assert ev.event == "payment.succeeded"
    assert ev.payment is not None
    assert ev.payment.id == "pay_1"
    assert ev.payment.is_succeeded


# ---------------- Фабрика-реестр (несколько провайдеров сразу) ----------------
@pytest.fixture(autouse=True)
def _reset_registry():
    # фабрика кэширует реестр синглтоном — сбрасываем вокруг каждого теста
    reset_acquirers()
    yield
    reset_acquirers()


def test_registry_collects_all_enabled(monkeypatch):
    monkeypatch.setattr(settings, "acquiring_memory_enabled", True)
    monkeypatch.setattr(settings, "yookassa_enabled", True)

    acquirers = get_acquirers()

    assert set(acquirers) == {AcquirerName.MEMORY, AcquirerName.YOOKASSA}
    assert isinstance(acquirers[AcquirerName.MEMORY], InMemoryAcquirer)
    assert isinstance(acquirers[AcquirerName.YOOKASSA], YooKassaAcquirer)
    # доступ по enum, без строк
    assert isinstance(get_acquirer(AcquirerName.YOOKASSA), YooKassaAcquirer)


def test_registry_only_enabled(monkeypatch):
    monkeypatch.setattr(settings, "acquiring_memory_enabled", True)
    monkeypatch.setattr(settings, "yookassa_enabled", False)
    assert set(get_acquirers()) == {AcquirerName.MEMORY}


def test_get_disabled_acquirer_raises(monkeypatch):
    monkeypatch.setattr(settings, "acquiring_memory_enabled", True)
    monkeypatch.setattr(settings, "yookassa_enabled", False)
    with pytest.raises(ValueError, match="not enabled"):
        get_acquirer(AcquirerName.YOOKASSA)


# ---------------- Валидация конфига на старте ----------------
def test_unknown_acquirer_name_is_error():
    # значение не из enum AcquirerName -> ошибка (нельзя сослаться на чужой провайдер)
    with pytest.raises(ValueError, match="paypal"):
        AcquirerName("paypal")


def test_memory_acquirer_forbidden_in_prod():
    with pytest.raises(ValidationError, match="ACQUIRING_MEMORY_ENABLED"):
        Settings(environment="prod", acquiring_memory_enabled=True, _env_file=None)


def test_memory_acquirer_allowed_in_dev():
    s = Settings(environment="dev", acquiring_memory_enabled=True, _env_file=None)
    assert s.acquiring_memory_enabled is True


def test_enabled_provider_without_credentials_is_startup_error():
    # YOOKASSA_ENABLED=true без кред -> падаем на старте, а не на первом платеже
    with pytest.raises(ValidationError, match="YOOKASSA_SHOP_ID"):
        Settings(environment="dev", yookassa_enabled=True, _env_file=None)


def test_enabled_provider_with_credentials_ok():
    s = Settings(
        environment="dev",
        yookassa_enabled=True,
        yookassa_shop_id="shop_1",
        yookassa_secret_key="secret_1",
        _env_file=None,
    )
    assert s.yookassa_enabled is True


# ---------------- Валидация acquirer в AmountRequest (HTTP-граница) ----------------
def test_amount_request_rejects_disabled_acquirer(monkeypatch):
    # провайдер из enum, но выключен в конфиге -> 422 (а не «платёж выключенной системой»)
    monkeypatch.setattr(settings, "acquiring_memory_enabled", False)
    monkeypatch.setattr(settings, "yookassa_enabled", False)
    with pytest.raises(ValidationError, match="not enabled"):
        AmountRequest(amount=100, acquirer=AcquirerName.MEMORY)


def test_amount_request_accepts_enabled_acquirer(monkeypatch):
    monkeypatch.setattr(settings, "acquiring_memory_enabled", True)
    monkeypatch.setattr(settings, "yookassa_enabled", False)
    req = AmountRequest(amount=100, acquirer=AcquirerName.MEMORY)
    assert req.acquirer is AcquirerName.MEMORY


def test_amount_request_unknown_acquirer_rejected():
    # значение не из enum AcquirerName -> 422 ещё до нашего валидатора (pydantic)
    with pytest.raises(ValidationError):
        AmountRequest(amount=100, acquirer="paypal")
