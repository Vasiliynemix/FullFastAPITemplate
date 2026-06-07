"""
Слой идемпотентности на Redis.

Клиент присылает заголовок `Idempotency-Key` для небезопасных операций (POST).
Логика:
* try_acquire — атомарно «застолбить» ключ (SET NX). Если ключ уже есть с готовым
  ответом — вернуть его (повтор). Если ключ в статусе "in progress" — конфликт.
* save_response — сохранить итоговый ответ под ключом с TTL.

Это защищает от дублей при ретраях клиента/сети — критично для платёжных и
создающих ресурсы эндпоинтов в высоконагруженных системах.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import orjson
from redis.asyncio import Redis

from app.cache.redis_cache import get_redis

_DEFAULT_TTL = 24 * 3600  # сутки
_IN_PROGRESS = b"__in_progress__"


@dataclass(slots=True)
class IdempotencyHit:
    found: bool
    in_progress: bool
    response: dict[str, Any] | None


class IdempotencyStore:
    def __init__(self, client: Redis | None = None, *, ttl: int = _DEFAULT_TTL) -> None:
        self._client = client or get_redis()
        self._ttl = ttl

    def _key(self, key: str) -> str:
        return f"idempotency:{key}"

    async def try_acquire(self, key: str) -> IdempotencyHit:
        rkey = self._key(key)
        # NX: ставим маркер in-progress, если ключа ещё нет
        acquired = await self._client.set(rkey, _IN_PROGRESS, nx=True, ex=self._ttl)
        if acquired:
            return IdempotencyHit(found=False, in_progress=False, response=None)

        existing = await self._client.get(rkey)
        if existing == _IN_PROGRESS:
            return IdempotencyHit(found=True, in_progress=True, response=None)
        # existing здесь гарантированно не None (ключ существует и != _IN_PROGRESS)
        return IdempotencyHit(found=True, in_progress=False, response=orjson.loads(existing))  # type: ignore[arg-type]

    async def save_response(self, key: str, response: dict[str, Any]) -> None:
        await self._client.set(self._key(key), orjson.dumps(response), ex=self._ttl)

    async def release(self, key: str) -> None:
        # Снять in-progress при ошибке, чтобы клиент мог повторить
        await self._client.delete(self._key(key))


_store: IdempotencyStore | None = None


def get_idempotency_store() -> IdempotencyStore:
    global _store
    if _store is None:
        _store = IdempotencyStore()
    return _store
