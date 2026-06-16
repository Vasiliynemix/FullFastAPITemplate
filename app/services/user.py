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

from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.orm.exc import StaleDataError

from app.broker.events import Event
from app.cache.base import AbstractCache
from app.db.query import sanitize_q
from app.db.uow import UnitOfWork
from app.decorators.logging import logged
from app.exceptions.base import ConflictError, NotFoundError
from app.models.profile import Profile
from app.models.user import User
from app.schemas.account import ProfileRead, ProfileUpsert
from app.schemas.user import UserCreate, UserRead, UserReadDetail, UserUpdate
from app.security.session_store import SessionStore

_CACHE_PREFIX = "user"
_CACHE_TTL = 30


def user_cache_key(user_id: uuid.UUID) -> str:
    """Ключ кэша одного пользователя (cache-aside в UserService). Экспортируем,
    чтобы AccountService мог инвалидировать его при изменении счетов/балансов
    пользователя — иначе GET /users/{id} до TTL отдаёт устаревшие счета."""
    return f"{_CACHE_PREFIX}:{user_id}"


# Eager-load связей для ДЕТАЛЬНОЙ формы (UserReadDetail). Нужен там, где связи
# реально отдаются (get/list); базовый UserRead их не грузит. selectinload для
# коллекции, joinedload для 1-1.
_USER_LOAD = (selectinload(User.accounts), joinedload(User.profile))


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
            dto = UserRead.model_validate(user)  # база: связи не читаем

        # В кэш кладём ДЕТАЛЬНУЮ форму (её отдаёт get) — у нового юзера связей ещё
        # нет, поэтому собираем без запроса к БД.
        await self._cache_put(UserReadDetail(**dto.model_dump(), accounts=[], profile=None))
        return dto

    @logged("user.get")
    async def get(self, user_id: uuid.UUID) -> UserReadDetail:
        # cache-aside: сначала кэш (хранится детальная форма)
        cached = await self.cache.get(self._key(user_id))
        if cached is not None:
            return UserReadDetail.model_validate(cached)

        async with self.uow:
            user = await self.uow.users.get(user_id, options=list(_USER_LOAD))
        if user is None:
            raise NotFoundError("User not found")

        dto = UserReadDetail.model_validate(user)
        await self._cache_put(dto)
        return dto

    @logged("user.list")
    async def list(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        filters: dict[str, str] | None = None,
        sort: str | None = None,
        q: str | None = None,
    ) -> tuple[Sequence[UserReadDetail], int]:
        # q санитизируем (срезаем HTML/control); пустую строку превращаем в None
        clean_q = sanitize_q(q) if q else None
        async with self.uow:
            users, total = await self.uow.users.paginate(
                page=page,
                per_page=per_page,
                filters=filters,
                sort=sort,
                q=clean_q or None,
                options=list(_USER_LOAD),
            )
        return [UserReadDetail.model_validate(u) for u in users], total

    @logged("user.update")
    async def update(self, user_id: uuid.UUID, data: UserUpdate) -> UserRead:
        values = data.model_dump(exclude_none=True)
        async with self.uow:
            user = await self.uow.users.get(user_id)
            if user is None:
                raise NotFoundError("User not found")
            for field, value in values.items():
                setattr(user, field, value)
            try:
                # Оптимистичная блокировка (VersionedMixin): если строку успел изменить
                # кто-то ещё, ORM кинет StaleDataError -> отдаём 409, клиент перечитает и повторит.
                await self.uow.commit()
            except StaleDataError as exc:
                raise ConflictError("User was modified concurrently, refresh and retry") from exc
            # updated_at проставляет БД (onupdate) → после commit поле «протухло»;
            # обновляем объект, чтобы model_validate не ушёл в ленивое чтение.
            await self.uow.session.refresh(user)
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

    @logged("user.upsert_profile")
    async def upsert_profile(self, user_id: uuid.UUID, data: ProfileUpsert) -> ProfileRead:
        """Создать или обновить профиль пользователя (демо one-to-one). Идемпотентно по сути."""
        # Переданы только реально присланные поля (PUT-партиал): exclude_unset
        values = data.model_dump(exclude_unset=True)
        async with self.uow:
            # profile грузим сразу (eager) — иначе доступ к user.profile
            # в async уронит MissingGreenlet.
            user = await self.uow.users.get(user_id, options=[selectinload(User.profile)])
            if user is None:
                raise NotFoundError("User not found")
            if user.profile is None:
                user.profile = Profile(**values)  # one-to-one: FK проставится из user.id при flush
            else:
                for field, value in values.items():
                    setattr(user.profile, field, value)
            await self.uow.commit()
            dto = ProfileRead.model_validate(user.profile)
        # Профиль входит в детальную форму юзера — сбрасываем его кэш.
        await self.cache.delete(self._key(user_id))
        return dto

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
        return user_cache_key(user_id)

    async def _cache_put(self, dto: UserReadDetail) -> None:
        # Кэшируем именно детальную форму — её отдаёт get (cache-aside).
        await self.cache.set(self._key(dto.id), dto.model_dump(mode="json"), ttl=_CACHE_TTL)
