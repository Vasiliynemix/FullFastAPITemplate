"""
Сервис пользователей — ТОЛЬКО бизнес-логика.

Правила слоя:
* доступ к БД — исключительно через UoW/репозитории;
* доступ к кэшу — через абстракцию AbstractCache;
* события — через AbstractBroker;
* ошибки наружу — ТОЛЬКО ServerException (контролируемые).

Демонстрирует cache-aside, инвалидацию кэша, публикацию события в брокер
и потоковую выдачу (генератор) для больших коллекций.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from typing import ClassVar

from app.broker.events import Event
from app.cache.base import AbstractCache
from app.db.uow import UnitOfWork
from app.decorators.logging import logged
from app.exceptions.base import ConflictError, NotFoundError
from app.models.user import User
from app.schemas.user import UserCreate, UserRead, UserUpdate
from app.security.session_store import SessionStore

_CACHE_PREFIX = "user"
_CACHE_TTL = 30


class UserCreated(Event):
    """Доменное событие: пользователь создан (контракт payload)."""

    topic: ClassVar[str] = "users.created"

    id: str
    email: str


class UserService:
    def __init__(
        self,
        uow: UnitOfWork,
        cache: AbstractCache,
        sessions: SessionStore | None = None,
    ) -> None:
        self.uow = uow
        self.cache = cache
        # sessions опционален: при удалении пользователя отзываем все его сессии
        self.sessions = sessions

    @logged("user.create")
    async def create(self, data: UserCreate) -> UserRead:
        async with self.uow:
            existing = await self.uow.users.get_by_email(data.email)
            if existing is not None:
                raise ConflictError("User with this email already exists")
            user = await self.uow.users.add(User(email=data.email, full_name=data.full_name))
            # Transactional outbox: событие пишем в ТУ ЖЕ транзакцию, что и пользователя.
            # Либо зафиксируется и юзер, и событие, либо ничего — потеря события исключена.
            # Реальную публикацию в брокер делает релей (app/outbox/relay.py) в воркере.
            await self.uow.outbox.add_event(UserCreated(id=str(user.id), email=user.email))
            await self.uow.commit()
            dto = UserRead.model_validate(user)

        await self._cache_put(dto)
        return dto

    @logged("user.get")
    async def get(self, user_id: uuid.UUID) -> UserRead:
        # cache-aside: сначала кэш
        cached = await self.cache.get(self._key(user_id))
        if cached is not None:
            return UserRead.model_validate(cached)

        async with self.uow:
            user = await self.uow.users.get(user_id)
        if user is None:
            raise NotFoundError("User not found")

        dto = UserRead.model_validate(user)
        await self._cache_put(dto)
        return dto

    @logged("user.list")
    async def list(self, *, limit: int = 50, offset: int = 0) -> tuple[Sequence[UserRead], int]:
        async with self.uow:
            users = await self.uow.users.list(limit=limit, offset=offset, order_by=User.created_at)
            total = await self.uow.users.count()
        return [UserRead.model_validate(u) for u in users], total

    @logged("user.update")
    async def update(self, user_id: uuid.UUID, data: UserUpdate) -> UserRead:
        values = data.model_dump(exclude_none=True)
        async with self.uow:
            user = await self.uow.users.get(user_id)
            if user is None:
                raise NotFoundError("User not found")
            for field, value in values.items():
                setattr(user, field, value)
            await self.uow.commit()
            dto = UserRead.model_validate(user)

        await self.cache.delete(self._key(user_id))  # инвалидация
        return dto

    @logged("user.delete")
    async def delete(self, user_id: uuid.UUID) -> None:
        async with self.uow:
            deleted = await self.uow.users.delete_by_id(user_id)
            if deleted == 0:
                raise NotFoundError("User not found")
            await self.uow.commit()
        await self.cache.delete(self._key(user_id))
        # Отзываем все сессии удалённого пользователя (refresh мгновенно мёртв;
        # access истечёт по TTL, либо сразу при AUTH_VALIDATE_SESSION=true)
        if self.sessions is not None:
            await self.sessions.revoke_all(str(user_id))

    async def stream_all(self) -> AsyncIterator[bytes]:
        """
        Потоковая выдача всех пользователей в формате NDJSON.
        Генератор + серверный курсор репозитория = константная память
        даже для миллионов строк (см. эндпоинт /users/stream).
        """
        async with self.uow:
            async for user in self.uow.users.stream(order_by=User.created_at):
                dto = UserRead.model_validate(user)
                yield dto.model_dump_json().encode() + b"\n"

    # ------------------------------------------------------------------
    def _key(self, user_id: uuid.UUID) -> str:
        return f"{_CACHE_PREFIX}:{user_id}"

    async def _cache_put(self, dto: UserRead) -> None:
        await self.cache.set(self._key(dto.id), dto.model_dump(mode="json"), ttl=_CACHE_TTL)
