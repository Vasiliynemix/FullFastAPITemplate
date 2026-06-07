"""
Redis-реализация кэша + общий клиент Redis.

Решения под нагрузку:
* Один пул соединений на процесс (redis.asyncio.ConnectionPool), переиспользуется.
* hiredis-парсер (ставится через redis[hiredis]) — быстрый разбор ответов.
* orjson для (де)сериализации значений — быстрее стандартного json.
* incr+expire одной транзакцией (pipeline) — атомарный счётчик для rate limit.

Клиент Redis также используется rate limiter'ом, idempotency и pub/sub.
"""

from __future__ import annotations

from typing import Any

import orjson
from redis.asyncio import Redis
from redis.asyncio.connection import ConnectionPool

from app.cache.base import AbstractCache
from app.core.config import settings

_pool: ConnectionPool | None = None
_client: Redis | None = None


def get_redis() -> Redis:
    """Ленивая инициализация общего клиента Redis (один на воркер)."""
    global _pool, _client
    if _client is None:
        _pool = ConnectionPool.from_url(
            settings.redis_url,
            max_connections=settings.redis_max_connections,
            decode_responses=False,  # храним bytes, сериализуем сами через orjson
        )
        _client = Redis(connection_pool=_pool)
    return _client


async def close_redis() -> None:
    global _client, _pool
    if _client is not None:
        await _client.aclose()
        _client = None
    if _pool is not None:
        await _pool.disconnect()
        _pool = None


class RedisCache(AbstractCache):
    def __init__(self, client: Redis | None = None) -> None:
        self._client = client or get_redis()
        self._default_ttl = settings.redis_default_ttl

    async def get(self, key: str) -> Any | None:
        raw = await self._client.get(key)
        if raw is None:
            return None
        return orjson.loads(raw)

    async def set(self, key: str, value: Any, *, ttl: int | None = None) -> None:
        payload = orjson.dumps(value)
        await self._client.set(key, payload, ex=ttl or self._default_ttl)

    async def delete(self, *keys: str) -> int:
        if not keys:
            return 0
        return await self._client.delete(*keys)

    async def exists(self, key: str) -> bool:
        return bool(await self._client.exists(key))

    async def incr(self, key: str, *, amount: int = 1, ttl: int | None = None) -> int:
        # Атомарно: инкремент + установка TTL только при первом обращении
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.incrby(key, amount)
            if ttl is not None:
                pipe.expire(key, ttl, nx=True)
            results = await pipe.execute()
        return int(results[0])


def get_redis_cache() -> RedisCache:
    return RedisCache()
