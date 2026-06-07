"""
Декоратор retry с экспоненциальной задержкой и джиттером.

Только для async-функций. Повторяет вызов при перечисленных исключениях.
Джиттер (детерминированный, без random) разводит «громовое стадо» ретраев.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.core.logging import get_logger

T = TypeVar("T")
logger = get_logger("retry")


def retry(
    *,
    attempts: int = 3,
    base_delay: float = 0.1,
    max_delay: float = 2.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: object, **kwargs: object) -> T:
            last_exc: BaseException | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == attempts:
                        break
                    # exp backoff, ограниченный max_delay; джиттер от номера попытки
                    delay = min(base_delay * 2 ** (attempt - 1), max_delay)
                    jitter = delay * 0.1 * (attempt % 3)
                    logger.warning(
                        "retry_attempt",
                        func=func.__name__,
                        attempt=attempt,
                        delay=round(delay + jitter, 3),
                        error=str(exc),
                    )
                    await asyncio.sleep(delay + jitter)
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
