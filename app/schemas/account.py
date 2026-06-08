"""
DTO демо-домена. Сериализуются из ORM (from_attributes), включая relationship —
поэтому в роутере связи ОБЯЗАТЕЛЬНО eager-загружены (иначе MissingGreenlet при сборке).
"""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field


class CategoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str


class TransactionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    amount: int
    kind: str
    created_at: datetime.datetime
    categories: list[CategoryRead] = []  # many-to-many


class AccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    balance: int


class AccountWithTransactions(AccountRead):
    transactions: list[TransactionRead] = []  # one-to-many


class ProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    bio: str | None = None
    avatar_url: str | None = None


class TransactionBrief(BaseModel):
    # Без categories — для overview, чтобы не грузить ещё уровень связи
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    amount: int
    kind: str
    created_at: datetime.datetime


class AccountBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    balance: int
    transactions: list[TransactionBrief] = []


class UserOverview(BaseModel):
    """Юзер целиком: профиль (1-1) + счета (1-many) + их транзакции (вложенно)."""

    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    full_name: str
    profile: ProfileRead | None = None
    accounts: list[AccountBrief] = []


# --- запросы ---
class CreateAccountRequest(BaseModel):
    user_id: uuid.UUID
    name: str = Field(min_length=1, max_length=255)


class AmountRequest(BaseModel):
    amount: int = Field(gt=0)  # минорные единицы (копейки); строго > 0
    category_ids: list[uuid.UUID] = []  # для депозита: пометить транзакцию категориями
