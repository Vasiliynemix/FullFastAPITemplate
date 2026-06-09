"""
Сервис демо-домена «счета».

Демонстрирует:
* пессимистичную блокировку (`for_update`) на операциях с балансом — против гонок списания;
* eager-load relationship (`get_with_transactions`, `get_overview`) — обязательный в async;
* запись many-to-many (привязка категорий к транзакции).

Деньги — целые минорные единицы (копейки). Наружу — только ServerException.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.exc import IntegrityError

from app.core.config import AcquirerName
from app.db.uow import UnitOfWork
from app.decorators.logging import logged
from app.exceptions.base import ConflictError, NotFoundError
from app.models.account import Account, Category, Transaction
from app.schemas.account import AccountRead, AccountWithTransactions, CategoryRead, UserOverview


class AccountService:
    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow

    @logged("category.create")
    async def create_category(self, name: str) -> CategoryRead:
        async with self.uow:
            try:
                cat = await self.uow.categories.add(Category(name=name))
                await self.uow.commit()  # name UNIQUE -> дубль даст IntegrityError
            except IntegrityError as exc:
                raise ConflictError("Category with this name already exists") from exc
            return CategoryRead.model_validate(cat)

    @logged("category.list")
    async def list_categories(self) -> list[CategoryRead]:
        async with self.uow:
            cats = await self.uow.categories.list(order_by=Category.name)
            return [CategoryRead.model_validate(c) for c in cats]

    @logged("account.create")
    async def create_account(self, user_id: uuid.UUID, name: str) -> AccountRead:
        async with self.uow:
            if await self.uow.users.get(user_id) is None:
                raise NotFoundError("User not found")
            acc = await self.uow.accounts.add(Account(user_id=user_id, name=name))
            await self.uow.commit()
            return AccountRead.model_validate(acc)

    @logged("account.get")
    async def get_account(self, account_id: uuid.UUID) -> AccountWithTransactions:
        async with self.uow:
            # eager-load: транзакции (one-to-many) + их категории (many-to-many)
            acc = await self.uow.accounts.get_with_transactions(account_id)
            if acc is None:
                raise NotFoundError("Account not found")
            # model_validate ВНУТРИ сессии — связи уже загружены, ленивого доступа нет
            return AccountWithTransactions.model_validate(acc)

    @logged("account.deposit")
    async def deposit(
        self,
        account_id: uuid.UUID,
        amount: int,
        acquirer: AcquirerName,
        category_ids: Sequence[uuid.UUID] = (),
    ) -> AccountRead:
        async with self.uow:
            # FOR UPDATE: блокируем строку счёта на время операции (без гонок баланса)
            acc = await self.uow.accounts.get(account_id, for_update=True)
            if acc is None:
                raise NotFoundError("Account not found")
            acc.balance += amount
            tx = Transaction(account_id=acc.id, amount=amount, kind="deposit", acquirer=acquirer)
            if category_ids:
                # many-to-many запись + ВАЛИДАЦИЯ: неизвестный category_id -> 404,
                # а не молчаливый пропуск (раньше get_many просто отбрасывал несуществующие)
                cats = await self.uow.categories.get_many(category_ids)
                if len(cats) != len(set(category_ids)):
                    raise NotFoundError("One or more categories not found")
                tx.categories = cats
            await self.uow.transactions.add(tx)
            await self.uow.commit()
            return AccountRead.model_validate(acc)

    @logged("account.withdraw")
    async def withdraw(
        self, account_id: uuid.UUID, amount: int, acquirer: AcquirerName
    ) -> AccountRead:
        async with self.uow:
            acc = await self.uow.accounts.get(account_id, for_update=True)
            if acc is None:
                raise NotFoundError("Account not found")
            if acc.balance < amount:
                # Лок гарантирует, что два параллельных снятия не пройдут оба
                raise ConflictError("Insufficient funds")
            acc.balance -= amount
            await self.uow.transactions.add(
                Transaction(account_id=acc.id, amount=-amount, kind="withdrawal", acquirer=acquirer)
            )
            await self.uow.commit()
            return AccountRead.model_validate(acc)

    @logged("account.overview")
    async def get_user_overview(self, user_id: uuid.UUID) -> UserOverview:
        async with self.uow:
            # вложенный eager-load: профиль (1-1) + счета (1-many) + их транзакции
            user = await self.uow.users.get_overview(user_id)
            if user is None:
                raise NotFoundError("User not found")
            return UserOverview.model_validate(user)
