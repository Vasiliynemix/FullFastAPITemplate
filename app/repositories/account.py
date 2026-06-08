"""
Репозитории демо-домена. Показывают eager-load relationship через `options=`
(в т.ч. вложенный selectinload) — обязательный в async приём, иначе MissingGreenlet.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.account import Account, Category, Transaction
from app.repositories.base import BaseRepository


class AccountRepository(BaseRepository[Account]):
    model = Account

    async def get_with_transactions(self, account_id: uuid.UUID) -> Account | None:
        # one-to-many (transactions) + вложенный many-to-many (их categories)
        return await self.get(
            account_id,
            options=[selectinload(Account.transactions).selectinload(Transaction.categories)],
        )


class TransactionRepository(BaseRepository[Transaction]):
    model = Transaction


class CategoryRepository(BaseRepository[Category]):
    model = Category

    async def get_many(self, ids: Sequence[uuid.UUID]) -> list[Category]:
        if not ids:
            return []
        stmt = select(Category).where(Category.id.in_(ids))
        return list((await self.session.execute(stmt)).scalars().all())
