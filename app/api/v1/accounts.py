"""
Демо-ручки «счета и транзакции» — показывают eager-load relationship в ответе и
пессимистичную блокировку (`for_update`) на операциях с деньгами.

* GET /accounts/{id}            — счёт + транзакции + их категории (one-to-many + many-to-many)
* GET /accounts/overview/{uid}  — юзер + профиль + счета + транзакции (1-1 + 1-many + вложенно)
* POST /accounts/{id}/deposit|withdraw — операции под FOR UPDATE
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import AccountServiceDep, RequiredIdempotencyKey
from app.idempotency import idempotent
from app.schemas.account import (
    AccountRead,
    AccountWithTransactions,
    AmountRequest,
    CreateAccountRequest,
    UserOverview,
)
from app.schemas.response import SuccessResponse, success

router = APIRouter()


@router.post("", response_model=SuccessResponse[AccountRead], status_code=status.HTTP_201_CREATED)
async def create_account(
    data: CreateAccountRequest,
    service: AccountServiceDep,
    idempotency_key: RequiredIdempotencyKey,
) -> SuccessResponse[AccountRead]:
    async def _produce() -> SuccessResponse[AccountRead]:
        return success(await service.create_account(data.user_id, data.name))

    return await idempotent(idempotency_key, SuccessResponse[AccountRead], _produce)


@router.get("/overview/{user_id}", response_model=SuccessResponse[UserOverview])
async def user_overview(
    user_id: uuid.UUID, service: AccountServiceDep
) -> SuccessResponse[UserOverview]:
    """Полный граф юзера: профиль (1-1) + счета (1-many) + их транзакции (вложенный eager-load)."""
    return success(await service.get_user_overview(user_id))


@router.get("/{account_id}", response_model=SuccessResponse[AccountWithTransactions])
async def get_account(
    account_id: uuid.UUID, service: AccountServiceDep
) -> SuccessResponse[AccountWithTransactions]:
    """Счёт + транзакции + категории каждой транзакции (всё eager-загружено)."""
    return success(await service.get_account(account_id))


@router.post("/{account_id}/deposit", response_model=SuccessResponse[AccountRead])
async def deposit(
    account_id: uuid.UUID,
    data: AmountRequest,
    service: AccountServiceDep,
    idempotency_key: RequiredIdempotencyKey,
) -> SuccessResponse[AccountRead]:
    # Деньги: ретрай по сети не должен задвоить операцию -> Idempotency-Key обязателен на проде
    async def _produce() -> SuccessResponse[AccountRead]:
        return success(
            await service.deposit(account_id, data.amount, data.acquirer, data.category_ids)
        )

    return await idempotent(idempotency_key, SuccessResponse[AccountRead], _produce)


@router.post("/{account_id}/withdraw", response_model=SuccessResponse[AccountRead])
async def withdraw(
    account_id: uuid.UUID,
    data: AmountRequest,
    service: AccountServiceDep,
    idempotency_key: RequiredIdempotencyKey,
) -> SuccessResponse[AccountRead]:
    async def _produce() -> SuccessResponse[AccountRead]:
        return success(await service.withdraw(account_id, data.amount, data.acquirer))

    return await idempotent(idempotency_key, SuccessResponse[AccountRead], _produce)
