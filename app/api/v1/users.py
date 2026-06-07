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
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUserDep, IdempotencyKey, UserServiceDep, require_roles
from app.idempotency.store import get_idempotency_store
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
    # Идемпотентность для безопасных ретраев создания
    if idempotency_key:
        store = get_idempotency_store()
        hit = await store.try_acquire(idempotency_key)
        if hit.found and hit.response is not None:
            return SuccessResponse[UserRead].model_validate(hit.response)
        try:
            created = await service.create(data)
        except Exception:
            await store.release(idempotency_key)
            raise
        response = success(created)
        await store.save_response(idempotency_key, response.model_dump(mode="json"))
        return response

    return success(await service.create(data))


@router.get("/{user_id}", response_model=SuccessResponse[UserRead])
async def get_user(
    user_id: uuid.UUID,
    service: UserServiceDep,
    _: CurrentUserDep,  # требует валидный access-токен (любая роль)
) -> SuccessResponse[UserRead]:
    return success(await service.get(user_id))


@router.get("", response_model=SuccessResponse[list[UserRead]])
async def list_users(
    service: UserServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=200)] = 50,
) -> SuccessResponse[list[UserRead]]:
    # Постраничная навигация: page (с 1) -> offset для слоя данных
    offset = (page - 1) * per_page
    items, total = await service.list(limit=per_page, offset=offset)
    pages = (total + per_page - 1) // per_page  # ceil(total / per_page)
    meta = ResponseMeta(page=page, per_page=per_page, total=total, pages=pages)
    return success(list(items), meta=meta)


@router.patch("/{user_id}", response_model=SuccessResponse[UserRead])
async def update_user(
    user_id: uuid.UUID,
    data: UserUpdate,
    service: UserServiceDep,
) -> SuccessResponse[UserRead]:
    return success(await service.update(user_id, data))


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
