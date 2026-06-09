"""
Фабрика эквайринга: реестр ВКЛЮЧЁННЫХ провайдеров (их может быть несколько сразу).

Ключи реестра — enum `AcquirerName` (не строки): в коде зовём `get_acquirer(AcquirerName.YOOKASSA)`,
опечатка в имени = ошибка типов, а не runtime-сюрприз. get_acquirers() собирает все включённые;
get_acquirer(name) достаёт нужный.

Добавить провайдера = значение в `AcquirerName` + флаг/креды в конфиге + одна ветка в
_build_enabled() (+ проверка кред в валидаторе конфига). SDK импортируется лениво.
"""

from __future__ import annotations

from typing import Any

from app.acquiring.base import AbstractAcquirer
from app.core.config import AcquirerName, settings

_acquirers: dict[AcquirerName, AbstractAcquirer[Any]] | None = None


def enabled_acquirers() -> set[AcquirerName]:
    """
    Имена ВКЛЮЧЁННЫХ провайдеров — живо из конфига, без построения инстансов.
    Единый источник истины «что включено» (его же использует валидатор AmountRequest).
    Добавить провайдера = его флаг сюда + ветка сборки в _build_one().
    """
    enabled: set[AcquirerName] = set()
    if settings.acquiring_memory_enabled:  # memory — заглушка (в проде запрещена валидатором)
        enabled.add(AcquirerName.MEMORY)
    if settings.yookassa_enabled:
        enabled.add(AcquirerName.YOOKASSA)
    return enabled


def _build_one(name: AcquirerName) -> AbstractAcquirer[Any]:
    if name is AcquirerName.MEMORY:
        from app.acquiring.memory import InMemoryAcquirer

        return InMemoryAcquirer(default_currency=settings.acquiring_currency)
    if name is AcquirerName.YOOKASSA:
        from app.acquiring.yookassa import YooKassaAcquirer

        return YooKassaAcquirer(
            shop_id=settings.yookassa_shop_id,
            secret_key=settings.yookassa_secret_key,
            default_currency=settings.acquiring_currency,
            default_return_url=settings.acquiring_return_url,
        )
    # ── добавить новый провайдер здесь:
    # if name is AcquirerName.STRIPE:
    #     from app.acquiring.stripe import StripeAcquirer
    #     return StripeAcquirer(...)
    raise ValueError(f"No builder for acquirer {name!r}")  # недостижимо при синхронных списках


def _build_enabled() -> dict[AcquirerName, AbstractAcquirer[Any]]:
    return {name: _build_one(name) for name in enabled_acquirers()}


def get_acquirers() -> dict[AcquirerName, AbstractAcquirer[Any]]:
    """Все включённые провайдеры (singleton на процесс), ключ — AcquirerName."""
    global _acquirers
    if _acquirers is None:
        _acquirers = _build_enabled()
    return _acquirers


def get_acquirer(name: AcquirerName) -> AbstractAcquirer[Any]:
    """Провайдер по имени-enum. ValueError, если он не включён в конфиге."""
    acquirers = get_acquirers()
    try:
        return acquirers[name]
    except KeyError:
        enabled = ", ".join(sorted(a.value for a in acquirers)) or "<none>"
        raise ValueError(f"Acquirer '{name.value}' is not enabled (enabled: {enabled})") from None


def reset_acquirers() -> None:
    global _acquirers
    _acquirers = None
