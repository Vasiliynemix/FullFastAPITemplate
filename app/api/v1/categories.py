"""
Категории транзакций (справочник many-to-many). Создание/список — чтобы их можно было
заводить из API, а не только в БД, и привязывать к депозитам (`POST /accounts/{id}/deposit`).
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import AccountServiceDep
from app.schemas.account import CategoryCreate, CategoryRead
from app.schemas.response import SuccessResponse, success

router = APIRouter()


@router.post("", response_model=SuccessResponse[CategoryRead], status_code=status.HTTP_201_CREATED)
async def create_category(
    data: CategoryCreate, service: AccountServiceDep
) -> SuccessResponse[CategoryRead]:
    """Создать категорию. Имя уникально — дубль вернёт 409."""
    return success(await service.create_category(data.name))


@router.get("", response_model=SuccessResponse[list[CategoryRead]])
async def list_categories(service: AccountServiceDep) -> SuccessResponse[list[CategoryRead]]:
    return success(await service.list_categories())
