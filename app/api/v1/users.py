"""
CRUD-эндпоинты пользователей.

В роутах НЕТ бизнес-логики — только: распарсить вход, вызвать сервис, обернуть
ответ в единый контракт. Все исключения уходят в глобальные хендлеры.

Демонстрирует:
* единый ServerResponse[T] во всех ответах;
* идемпотентность POST через заголовок Idempotency-Key;
* StreamingResponse + генератор для выгрузки больших коллекций (NDJSON).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse

from app.api.deps import (
    CurrentUserDep,
    IdempotencyKey,
    ListParamsDep,
    UserServiceDep,
    require_roles,
)
from app.idempotency import idempotent
from app.schemas.account import ProfileRead, ProfileUpsert
from app.schemas.response import (
    EmptyResponse,
    ResponseMeta,
    SuccessResponse,
    empty,
    success,
)
from app.schemas.user import UserCreate, UserRead, UserUpdate
from app.security.roles import Role

router = APIRouter()

# Примеры разных уровней защиты ручек:
# * create/list — открыты (или закрыты глобальным API-key gate, если включён);
# * get/{id} — требует любого авторизованного пользователя (JWT);
# * delete — только роль ADMIN.
# Меняйте по своим доменным правилам — это лишь демонстрация.


@router.post(
    "",
    response_model=SuccessResponse[UserRead],
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    data: UserCreate,
    service: UserServiceDep,
    idempotency_key: IdempotencyKey = None,
) -> SuccessResponse[UserRead]:
    # Идемпотентность опциональна: при наличии ключа ретраи безопасны (логика в idempotent()).
    async def _produce() -> SuccessResponse[UserRead]:
        return success(await service.create(data))

    return await idempotent(idempotency_key, SuccessResponse[UserRead], _produce)


@router.get("/{user_id}", response_model=SuccessResponse[UserRead])
async def get_user(
    user_id: uuid.UUID,
    service: UserServiceDep,
    _: CurrentUserDep,  # требует валидный access-токен (любая роль)
) -> SuccessResponse[UserRead]:
    return success(await service.get(user_id))


@router.get("", response_model=SuccessResponse[list[UserRead]])
async def list_users(
    service: UserServiceDep, params: ListParamsDep
) -> SuccessResponse[list[UserRead]]:
    """Список пользователей. Пагинация/фильтры/сортировка/поиск — см. описание API сверху."""
    items, total = await service.list(
        page=params.page,
        per_page=params.per_page,
        filters=params.filters,
        sort=params.sort,
        q=params.q,
    )
    pages = (total + params.per_page - 1) // params.per_page  # ceil(total / per_page)
    meta = ResponseMeta(page=params.page, per_page=params.per_page, total=total, pages=pages)
    return success(list(items), meta=meta)


@router.patch("/{user_id}", response_model=SuccessResponse[UserRead])
async def update_user(
    user_id: uuid.UUID,
    data: UserUpdate,
    service: UserServiceDep,
) -> SuccessResponse[UserRead]:
    return success(await service.update(user_id, data))


@router.put("/{user_id}/profile", response_model=SuccessResponse[ProfileRead])
async def upsert_profile(
    user_id: uuid.UUID,
    data: ProfileUpsert,
    service: UserServiceDep,
) -> SuccessResponse[ProfileRead]:
    """Создать/обновить профиль пользователя (демо связи one-to-one User ↔ Profile)."""
    return success(await service.upsert_profile(user_id, data))


@router.delete(
    "/{user_id}",
    response_model=EmptyResponse,
    dependencies=[Depends(require_roles(Role.ADMIN))],  # только администратор
)
async def delete_user(user_id: uuid.UUID, service: UserServiceDep) -> EmptyResponse:
    await service.delete(user_id)
    return empty()


@router.get("/stream/all")
async def stream_users(service: UserServiceDep) -> StreamingResponse:
    # Потоковая выдача NDJSON — память не растёт с числом строк
    return StreamingResponse(service.stream_all(), media_type="application/x-ndjson")
