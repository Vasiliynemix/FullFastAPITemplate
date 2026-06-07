"""
Декоратор cached — кэширование результата async-функции в Redis (cache-aside).

Ключ строится из префикса + позиционных/именованных аргументов (кроме self).
Значение сериализуется orjson. При miss — вызывается функция и результат пишется в кэш.

Использование:
    @cached(prefix="user", ttl=30, key_args=["user_id"])
    async def get_user(self, user_id: str) -> dict: ...
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.cache.redis_cache import get_redis_cache
from app.core.logging import get_logger

T = TypeVar("T")
logger = get_logger("cache")


def _build_key(prefix: str, key_args: list[str] | None, args: tuple, kwargs: dict) -> str:
    if key_args:
        parts = [str(kwargs[a]) for a in key_args if a in kwargs]
    else:
        # Пропускаем self/cls (первый позиционный), берём остальные
        parts = [str(a) for a in args[1:]] + [f"{k}={v}" for k, v in sorted(kwargs.items())]
    return f"{prefix}:" + ":".join(parts)


def cached(
    *,
    prefix: str,
    ttl: int = 60,
    key_args: list[str] | None = None,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: object, **kwargs: object) -> T:
            cache = get_redis_cache()
            key = _build_key(prefix, key_args, args, kwargs)

            cached_value = await cache.get(key)
            if cached_value is not None:
                logger.debug("cache_hit", key=key)
                return cached_value  # type: ignore[return-value]

            result = await func(*args, **kwargs)
            # None не кэшируем, чтобы не закреплять «промахи»
            if result is not None:
                await cache.set(key, result, ttl=ttl)
            logger.debug("cache_miss", key=key)
            return result

        return wrapper

    return decorator
