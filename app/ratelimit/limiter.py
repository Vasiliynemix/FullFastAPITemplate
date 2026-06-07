"""
Распределённый многоярусный rate limiter на Redis (fixed window counters).

Ярусы — это набор окон (limit, window). Запрос проходит, только если уложился во
ВСЕ ярусы; блокируется, если превышен ХОТЯ БЫ ОДИН. Типичная конфигурация:
* длинное окно  — 1000 / 60с (минутная квота);
* короткое (burst) окно — 20 / 1с (сглаживает всплески, не даёт «выстрелить»
  всю минутную квоту за секунду).

Проверка всех ярусов — один atomic Lua-скрипт (один round-trip в Redis):
* атомарно (нет гонок INCR/EXPIRE);
* распределённо (счётчики в общем Redis — единый лимит на все инстансы).

Замечание про fixed window: окно привязано к первому запросу; на стыке окон
возможен всплеск до ~2*limit. Короткий burst-ярус как раз ограничивает такие всплески.
Заблокированный запрос всё равно инкрементит счётчики всех ярусов (штраф за спам).
"""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from app.cache.redis_cache import get_redis
from app.core.config import settings

# KEYS = ключи счётчиков по ярусам; ARGV = [limit1, window1, limit2, window2, ...].
# Возвращает {blocked(0/1), retry_after(сек), min_remaining}.
_LUA_MULTI_WINDOW = """
local blocked = 0
local retry = 0
local min_remaining = -1
for i = 1, #KEYS do
    local limit = tonumber(ARGV[(i - 1) * 2 + 1])
    local window = tonumber(ARGV[(i - 1) * 2 + 2])
    local current = redis.call('INCR', KEYS[i])
    if current == 1 then
        redis.call('EXPIRE', KEYS[i], window)
    end
    local remaining = limit - current
    if remaining < 0 then
        blocked = 1
        local ttl = redis.call('TTL', KEYS[i])
        if ttl > retry then retry = ttl end
    end
    if min_remaining == -1 or remaining < min_remaining then
        min_remaining = remaining
    end
end
return {blocked, retry, min_remaining}
"""


@dataclass(slots=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after: int
    limit: int


@dataclass(slots=True)
class _Tier:
    name: str
    limit: int
    window: int


class RateLimiter:
    def __init__(
        self,
        client: Redis | None = None,
        *,
        tiers: list[_Tier] | None = None,
    ) -> None:
        self._client = client or get_redis()
        self._tiers = tiers if tiers is not None else self._tiers_from_settings()
        self._script = self._client.register_script(_LUA_MULTI_WINDOW)

    @staticmethod
    def _tiers_from_settings() -> list[_Tier]:
        tiers = [_Tier("win", settings.rate_limit_requests, settings.rate_limit_window)]
        # Короткий burst-ярус подключается только если задан (>0)
        if settings.rate_limit_burst > 0:
            tiers.append(
                _Tier("burst", settings.rate_limit_burst, settings.rate_limit_burst_window)
            )
        return tiers

    async def check(self, identity: str) -> RateLimitResult:
        keys = [f"ratelimit:{t.name}:{identity}" for t in self._tiers]
        args: list[int] = []
        for t in self._tiers:
            args += [t.limit, t.window]

        blocked, retry, remaining = await self._script(keys=keys, args=args)
        blocked, retry, remaining = int(blocked), int(retry), int(remaining)

        return RateLimitResult(
            allowed=blocked == 0,
            remaining=max(0, remaining),  # самый «узкий» остаток по ярусам
            retry_after=retry if blocked else 0,
            limit=self._tiers[0].limit,  # headline-лимит (длинное окно)
        )


_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter
