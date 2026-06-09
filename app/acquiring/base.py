"""
Абстракция платёжного эквайринга.

Сервисы работают с платежами через AbstractAcquirer, не зная о конкретном провайдере
(YooKassa, Stripe, …) — так же, как с брокером (AbstractBroker) и хранилищем
(AbstractStorage). Это позволяет сменить провайдера, не трогая бизнес-логику.

Модели (Payment/Refund/WebhookEvent) дженерики по RawT — это тип «сырого» нативного
ответа провайдера в поле `raw`:
* провайдер С SDK  -> raw = объект SDK (его и используем, свой HTTP-клиент НЕ нужен);
* провайдер БЕЗ SDK -> клиент на app/clients/BaseHTTPClient, raw = dict (сырой JSON).
В обоих случаях НАРУЖУ отдаём одни и те же доменные модели — обращение всегда через мой
интерфейс (payment.id/.status/.amount/...), а `raw` доступен для провайдер-специфики.

Реализации: app/acquiring/yookassa.py (SDK), app/acquiring/memory.py (заглушка для
тестов/локалки). Систем может быть несколько сразу — каждая за своим флагом; фабрика
app/acquiring/factory.py собирает все включённые в реестр {name: instance}.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Generic, TypeVar

from app.exceptions.base import ServerException
from app.schemas.response import ErrorCode

# Тип «сырого» ответа провайдера (объект SDK или dict). Доменный код его не трогает.
RawT = TypeVar("RawT")


class AcquiringError(ServerException):
    """
    Сбой на стороне провайдера эквайринга. Наследник ServerException -> непойманная
    ошибка превратится в чистый 502 (а не сырой traceback). Для «платёж не найден»
    используйте NotFoundError из app.exceptions.base.
    """

    def __init__(self, message: str = "Acquiring provider error", **kw: object) -> None:
        super().__init__(502, message, code=ErrorCode.UNAVAILABLE, **kw)  # type: ignore[arg-type]


class PaymentStatus(StrEnum):
    """Унифицированный статус платежа (значения совпадают со статусами YooKassa)."""

    PENDING = "pending"  # создан, ждём оплаты/подтверждения пользователем
    WAITING_FOR_CAPTURE = "waiting_for_capture"  # холд, нужен capture (двухстадийный)
    SUCCEEDED = "succeeded"  # оплачен (деньги списаны)
    CANCELED = "canceled"  # отменён/просрочен/отклонён


@dataclass(slots=True)
class Payment(Generic[RawT]):
    """Доменная модель платежа — единый вид для любого провайдера."""

    id: str
    status: PaymentStatus
    amount: Decimal
    currency: str
    paid: bool
    confirmation_url: str | None  # куда редиректить пользователя для оплаты (если нужно)
    description: str | None
    metadata: dict[str, str]
    raw: RawT  # нативный ответ провайдера (SDK-объект или dict)

    @property
    def is_succeeded(self) -> bool:
        return self.status == PaymentStatus.SUCCEEDED


@dataclass(slots=True)
class Refund(Generic[RawT]):
    """Доменная модель возврата."""

    id: str
    payment_id: str
    status: str
    amount: Decimal
    currency: str
    raw: RawT


@dataclass(slots=True)
class WebhookEvent(Generic[RawT]):
    """Разобранное входящее уведомление провайдера (вебхук)."""

    event: str  # тип события, напр. "payment.succeeded" / "refund.succeeded"
    payment: Payment[RawT] | None  # платёж из тела уведомления (если применимо)
    raw: RawT  # сырое тело уведомления


class AbstractAcquirer(ABC, Generic[RawT]):
    """
    Контракт эквайринга. Все сетевые методы async. Реализация маппит ответ провайдера
    в доменные Payment/Refund. Ошибки провайдера -> AcquiringError (или NotFoundError).
    """

    # connect/close нужны провайдерам с долгоживущим клиентом/конфигом (SDK их использует).
    # По умолчанию — no-op, как у AbstractStorage.
    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def healthcheck(self) -> bool:
        """Доступен ли провайдер (для /health/ready). По умолчанию True; сетевые переопределяют."""
        return True

    @abstractmethod
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
    ) -> Payment[RawT]:
        """
        Создать платёж. `capture=True` — одностадийный (списать сразу после оплаты);
        `False` — двухстадийный (деньги холдируются, затем `capture_payment`).
        `idempotency_key` — ключ идемпотентности провайдера (безопасный ретрай создания).
        В ответе `confirmation_url` — куда отправить пользователя для оплаты.
        """

    @abstractmethod
    async def get_payment(self, payment_id: str) -> Payment[RawT]:
        """Получить платёж по id (опрос статуса). NotFoundError, если платежа нет."""

    @abstractmethod
    async def capture_payment(
        self,
        payment_id: str,
        *,
        amount: Decimal | None = None,
        idempotency_key: str | None = None,
    ) -> Payment[RawT]:
        """Подтвердить (списать) ранее захолдированный платёж. amount=None -> вся сумма."""

    @abstractmethod
    async def cancel_payment(
        self, payment_id: str, *, idempotency_key: str | None = None
    ) -> Payment[RawT]:
        """Отменить платёж (вернуть холд) — для статуса waiting_for_capture."""

    @abstractmethod
    async def create_refund(
        self,
        payment_id: str,
        *,
        amount: Decimal | None = None,
        idempotency_key: str | None = None,
    ) -> Refund[RawT]:
        """Вернуть деньги по оплаченному платежу. amount=None -> полный возврат."""

    @abstractmethod
    def parse_webhook(self, body: bytes) -> WebhookEvent[RawT]:
        """
        Разобрать тело входящего вебхука в WebhookEvent. Синхронный (только парсинг).
        ВАЖНО: вебхук — это лишь сигнал; перед действиями статус стоит перепроверить
        через get_payment (источник истины), т.к. уведомление можно подделать.
        """
