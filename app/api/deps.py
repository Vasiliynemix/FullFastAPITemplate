"""
Зависимости FastAPI (Dependency Injection).

Оптимизация DI под нагрузку:
* sessionmaker/redis/broker — процессные singletools (созданы один раз), сюда
  приходят уже готовыми; на запрос создаётся только лёгкий UoW и сервис.
* UoW не открывает соединение заранее — оно берётся из пула лениво в `async with`.
* Аутентификация access-токена — stateless (без обращения к БД/Redis): принципал
  собирается из клеймов JWT. Это держит горячий путь авторизации дешёвым.

Здесь же — фабрики проверки ролей (require_roles / require_at_least).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.broker.base import AbstractBroker
from app.broker.events import EventBus
from app.broker.factory import get_broker
from app.cache.base import AbstractCache
from app.cache.redis_cache import get_redis_cache
from app.clients.messages import MessagesClient, get_messages_client
from app.core.config import settings
from app.db.session import get_sessionmaker
from app.db.uow import UnitOfWork
from app.exceptions.base import ForbiddenError, UnauthorizedError
from app.security.jwt import TokenError, TokenType, decode_token
from app.security.roles import Role, role_at_least
from app.security.session_store import SessionStore, get_session_store
from app.services.auth import AuthService
from app.services.notification import NotificationService
from app.services.user import UserService
from app.storage.base import AbstractStorage
from app.storage.factory import get_storage as _get_storage

# auto_error=False -> сами бросаем единый ErrorResponse, а не дефолтный 403
_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Базовые ресурсы
# ---------------------------------------------------------------------------
def get_uow() -> UnitOfWork:
    # Лёгкий объект; соединение из пула берётся только внутри `async with`
    return UnitOfWork(get_sessionmaker())


def get_cache() -> AbstractCache:
    return get_redis_cache()


def get_message_broker() -> AbstractBroker:
    return get_broker()


def get_event_bus() -> EventBus:
    # Типизированная обёртка над брокером (publish/subscribe по моделям событий)
    return EventBus(get_broker())


def get_storage() -> AbstractStorage:
    # Singleton хранилища (подключён в lifespan). Реализация — по STORAGE_TYPE.
    return _get_storage()


def get_sessions() -> SessionStore:
    return get_session_store()


def get_user_service(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    cache: Annotated[AbstractCache, Depends(get_cache)],
    sessions: Annotated[SessionStore, Depends(get_sessions)],
) -> UserService:
    # sessions нужен, чтобы при удалении пользователя отозвать все его сессии.
    # Событие UserCreated пишется в outbox внутри транзакции (см. UserService.create).
    return UserService(uow=uow, cache=cache, sessions=sessions)


def get_auth_service(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    sessions: Annotated[SessionStore, Depends(get_sessions)],
) -> AuthService:
    return AuthService(uow=uow, sessions=sessions)


def get_notification_service(
    messages: Annotated[MessagesClient, Depends(get_messages_client)],
) -> NotificationService:
    # MessagesClient — процессный синглтон (пул соединений); сервис лёгкий, на запрос
    return NotificationService(messages=messages)


# ---------------------------------------------------------------------------
# Аутентификация / авторизация
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class CurrentUser:
    """Принципал текущего запроса (собран из клеймов access-токена)."""

    id: uuid.UUID
    role: Role
    sid: str | None  # id сессии — для logout текущей/прочих без refresh-токена


# Аноним для режима «только глобальный ключ» (JWT выключен). Роль SERVICE —
# это доверенный машинный доступ (запрос уже прошёл gate по X-API-Key).
_ANONYMOUS = CurrentUser(id=uuid.UUID(int=0), role=Role.SERVICE, sid=None)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    sessions: Annotated[SessionStore, Depends(get_sessions)],
) -> CurrentUser:
    # JWT выключен (режим «только глобальный ключ») — токен не требуется,
    # доступ уже разрешён глобальным gate; отдаём анонимного service-принципала.
    if not settings.auth_jwt_enabled:
        return _ANONYMOUS

    if credentials is None or not credentials.credentials:
        raise UnauthorizedError("Missing bearer token")
    try:
        payload = decode_token(credentials.credentials, expected_type=TokenType.ACCESS)
    except TokenError as exc:
        raise UnauthorizedError(str(exc)) from exc

    # Опционально (AUTH_VALIDATE_SESSION): сверяем sid с хранилищем — это даёт
    # немедленную инвалидацию access после logout/удаления ценой одного GET в Redis.
    if settings.auth_validate_session and (
        not payload.sid or not await sessions.exists(payload.sid)
    ):
        raise UnauthorizedError("Session revoked or expired")

    return CurrentUser(
        id=uuid.UUID(payload.sub),
        role=payload.role or Role.USER,
        sid=payload.sid,
    )


def require_roles(*roles: Role):
    """
    Зависимость-фабрика: доступ только перечисленным ролям (точное совпадение).
    Пример: dependencies=[Depends(require_roles(Role.ADMIN, Role.SERVICE))]
    """
    allowed = set(roles)

    async def _checker(
        user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        # При выключенном JWT роли не проверяем — доступ уже дан глобальным ключом
        if not settings.auth_jwt_enabled:
            return user
        if user.role not in allowed:
            raise ForbiddenError("Insufficient role")
        return user

    return _checker


def require_at_least(minimum: Role):
    """Зависимость-фабрика: доступ ролям с уровнем не ниже `minimum` (иерархия)."""

    async def _checker(
        user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        if not settings.auth_jwt_enabled:
            return user
        if not role_at_least(user.role, minimum):
            raise ForbiddenError("Insufficient role level")
        return user

    return _checker


# ---------------------------------------------------------------------------
# Готовые аннотированные типы
# ---------------------------------------------------------------------------
UserServiceDep = Annotated[UserService, Depends(get_user_service)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
NotificationServiceDep = Annotated[NotificationService, Depends(get_notification_service)]
BrokerDep = Annotated[AbstractBroker, Depends(get_message_broker)]
EventBusDep = Annotated[EventBus, Depends(get_event_bus)]
StorageDep = Annotated[AbstractStorage, Depends(get_storage)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]
