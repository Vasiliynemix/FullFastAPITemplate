"""
Декоратор transactional — оборачивает метод сервиса в транзакцию UoW.

Ожидает, что объект (self) имеет атрибут `uow` (UnitOfWork) или метод получает
uow первым именованным аргументом. Делает commit при успехе и rollback при ошибке.

Это синтаксический сахар поверх `async with uow`. В большинстве случаев сервис
сам открывает UoW; декоратор удобен для простых однотранзакционных методов.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.core.logging import get_logger
from app.db.uow import UnitOfWork

T = TypeVar("T")
logger = get_logger("transaction")


def transactional(
    func: Callable[..., Awaitable[T]],
) -> Callable[..., Awaitable[T]]:
    @functools.wraps(func)
    async def wrapper(self: object, *args: object, **kwargs: object) -> T:
        uow: UnitOfWork | None = getattr(self, "uow", None) or kwargs.get("uow")  # type: ignore[assignment]
        if uow is None:
            raise RuntimeError("@transactional requires `self.uow` or `uow=` kwarg")
        try:
            result = await func(self, *args, **kwargs)
            await uow.commit()
            return result
        except Exception:
            await uow.rollback()
            logger.warning("transaction_rolled_back", func=func.__name__)
            raise

    return wrapper
