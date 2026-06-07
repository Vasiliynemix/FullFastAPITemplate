"""
Декоратор logged — структурный лог вызова с латентностью.

Замеряет время через perf_counter (монотонные часы), логирует успех/ошибку.
Лёгкий: не сериализует аргументы целиком (это дорого в hot paths).
"""

from __future__ import annotations

import functools
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.core.logging import caller_location, get_logger

T = TypeVar("T")


def logged(
    event: str | None = None,
    *,
    level: str = "info",
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        log = get_logger(func.__module__)
        name = event or func.__name__
        # caller указывает на ОПРЕДЕЛЕНИЕ метода (а не место вызова) — так в логе
        # сразу видно, какой метод отработал/упал. Вычисляется один раз при декорировании.
        caller = caller_location(func.__code__.co_filename, func.__code__.co_firstlineno)

        @functools.wraps(func)
        async def wrapper(*args: object, **kwargs: object) -> T:
            start = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
            except Exception as exc:
                elapsed = (time.perf_counter() - start) * 1000
                log.error(
                    name,
                    status="error",
                    latency_ms=round(elapsed, 2),
                    error=str(exc),
                    caller=caller,
                )
                raise
            elapsed = (time.perf_counter() - start) * 1000
            getattr(log, level)(name, status="ok", latency_ms=round(elapsed, 2), caller=caller)
            return result

        return wrapper

    return decorator
