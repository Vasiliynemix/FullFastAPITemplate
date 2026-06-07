"""
Хранилище сессий в Redis.

Сессия = одно «вход­ное устройство/клиент». Идентифицируется sid (живёт и в access,
и в refresh токенах одной сессии). Внутри сессии refresh ротируется (новый jti),
а sid остаётся прежним — поэтому logout текущей сессии возможен по одному access.

Схема ключей:
* sess:{sid}        -> hash {uid, jti}, TTL = срок жизни refresh. Существование = активна.
* usess:{user_id}   -> SET sid'ов пользователя (индекс для «выйти из всех/прочих»).

Безопасность:
* validate сверяет uid и текущий jti — старый (ротированный) refresh не пройдёт (reuse).
* revoke_all вызывается при удалении/блокировке пользователя.

Замечание про access: access stateless и живёт коротко (минуты). После отзыва
сессии refresh сразу мёртв (новый access не получить), а уже выданный access
истечёт по TTL. Для немедленной инвалидации access включите AUTH_VALIDATE_SESSION
(тогда каждый запрос сверяет sid с этим хранилищем — один GET в Redis).
"""

from __future__ import annotations

import datetime
import uuid

from redis.asyncio import Redis

from app.cache.redis_cache import get_redis
from app.core.config import settings


def _iso_now() -> str:
    return datetime.datetime.now(tz=datetime.UTC).isoformat()


class SessionStore:
    def __init__(self, client: Redis | None = None) -> None:
        self._c = client or get_redis()
        self._ttl = settings.refresh_token_expire_days * 24 * 3600

    def _sk(self, sid: str) -> str:
        return f"sess:{sid}"

    def _uk(self, user_id: str) -> str:
        return f"usess:{user_id}"

    @staticmethod
    def _dec(value: bytes | str | None) -> str:
        if value is None:
            return ""
        return value.decode() if isinstance(value, bytes) else value

    async def create(
        self,
        user_id: str,
        *,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[str, str]:
        """Создать новую сессию (с метаданными). Возвращает (sid, jti) для токенов."""
        sid = uuid.uuid4().hex
        jti = uuid.uuid4().hex
        now = _iso_now()
        mapping = {
            "uid": user_id,
            "jti": jti,
            "created_at": now,
            "last_used": now,
            "ip": ip or "",
            "ua": (user_agent or "")[:256],  # обрезаем длинный User-Agent
        }
        async with self._c.pipeline(transaction=True) as p:
            p.hset(self._sk(sid), mapping=mapping)  # type: ignore[arg-type]  # redis stub строже
            p.expire(self._sk(sid), self._ttl)
            p.sadd(self._uk(user_id), sid)
            p.expire(self._uk(user_id), self._ttl)
            await p.execute()
        return sid, jti

    async def validate(self, sid: str, jti: str, user_id: str) -> bool:
        """Активна ли сессия и совпадает ли текущий refresh jti (защита от reuse)."""
        data = await self._c.hgetall(self._sk(sid))
        if not data:
            return False
        return self._dec(data.get(b"uid")) == user_id and self._dec(data.get(b"jti")) == jti

    async def rotate(self, sid: str, user_id: str) -> str | None:
        """Ротация refresh внутри сессии: новый jti, sid тот же. None если сессии нет."""
        data = await self._c.hgetall(self._sk(sid))
        if not data or self._dec(data.get(b"uid")) != user_id:
            return None
        new_jti = uuid.uuid4().hex
        async with self._c.pipeline(transaction=True) as p:
            p.hset(self._sk(sid), mapping={"jti": new_jti, "last_used": _iso_now()})
            p.expire(self._sk(sid), self._ttl)
            p.expire(self._uk(user_id), self._ttl)
            await p.execute()
        return new_jti

    async def exists(self, sid: str) -> bool:
        return bool(await self._c.exists(self._sk(sid)))

    async def revoke(self, sid: str) -> int:
        """Отозвать одну сессию. Возвращает 1 если была активна, иначе 0."""
        if not sid:
            return 0
        uid = self._dec(await self._c.hget(self._sk(sid), "uid"))
        if not uid:
            return 0
        async with self._c.pipeline(transaction=True) as p:
            p.delete(self._sk(sid))
            p.srem(self._uk(uid), sid)
            await p.execute()
        return 1

    async def list_sids(self, user_id: str) -> list[str]:
        sids = await self._c.smembers(self._uk(user_id))
        return [self._dec(s) for s in sids]

    async def list_sessions(self, user_id: str) -> list[dict[str, str | None]]:
        """Активные сессии пользователя с метаданными (для UI «мои устройства»)."""
        out: list[dict[str, str | None]] = []
        for sid in await self.list_sids(user_id):
            data = await self._c.hgetall(self._sk(sid))
            if not data:  # протухшая по TTL запись — пропускаем
                continue
            out.append(
                {
                    "sid": sid,
                    "created_at": self._dec(data.get(b"created_at")) or None,
                    "last_used_at": self._dec(data.get(b"last_used")) or None,
                    "ip": self._dec(data.get(b"ip")) or None,
                    "user_agent": self._dec(data.get(b"ua")) or None,
                }
            )
        out.sort(key=lambda s: s["created_at"] or "")
        return out

    async def revoke_all(self, user_id: str) -> int:
        """Отозвать ВСЕ сессии пользователя (logout all / удаление юзера)."""
        sids = await self.list_sids(user_id)
        if not sids:
            return 0
        async with self._c.pipeline(transaction=True) as p:
            for sid in sids:
                p.delete(self._sk(sid))
            p.delete(self._uk(user_id))
            await p.execute()
        return len(sids)

    async def revoke_others(self, user_id: str, keep_sid: str) -> int:
        """Отозвать все сессии, КРОМЕ текущей (keep_sid)."""
        others = [s for s in await self.list_sids(user_id) if s != keep_sid]
        if not others:
            return 0
        async with self._c.pipeline(transaction=True) as p:
            for sid in others:
                p.delete(self._sk(sid))
                p.srem(self._uk(user_id), sid)
            await p.execute()
        return len(others)


def get_session_store() -> SessionStore:
    return SessionStore()
