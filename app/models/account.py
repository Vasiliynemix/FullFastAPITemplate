"""
Демо-домен «счета и транзакции» — показывает ВСЕ типы связей SQLAlchemy и eager-load.

Связи:
* Account.user        — many-to-one  (много счетов у одного юзера)
* User.accounts       — one-to-many  (обратная сторона; объявлена в models/user.py)
* Account.transactions— one-to-many
* Transaction.account — many-to-one
* Transaction.categories <-> Category.transactions — many-to-many (через assoc-таблицу)
* User.profile <-> Profile.user — one-to-one (см. models/profile.py)

balance/amount — в МИНОРНЫХ единицах (копейки) целым числом: деньги во float хранить нельзя.
Операции с балансом берут строку FOR UPDATE (см. app/services/account.py) — пессимистичная
блокировка против гонок списания.
"""

from __future__ import annotations

import datetime
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, String, Table, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User

# Ассоциативная таблица для many-to-many (Transaction <-> Category).
transaction_categories = Table(
    "transaction_categories",
    Base.metadata,
    Column("transaction_id", ForeignKey("transactions.id", ondelete="CASCADE"), primary_key=True),
    Column("category_id", ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
)


class Account(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    balance: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)

    # many-to-one: счёт принадлежит одному юзеру
    user: Mapped[User] = relationship(back_populates="accounts")
    # one-to-many: у счёта много транзакций
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
        order_by="Transaction.created_at",
    )


class Transaction(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "transactions"

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)  # + депозит, - снятие
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # deposit | withdrawal
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(), server_default=func.now(), nullable=False
    )

    # many-to-one: транзакция принадлежит одному счёту
    account: Mapped[Account] = relationship(back_populates="transactions")
    # many-to-many: у транзакции много категорий, у категории — много транзакций
    categories: Mapped[list[Category]] = relationship(
        secondary=transaction_categories, back_populates="transactions"
    )


class Category(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "categories"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)

    # many-to-many (обратная сторона)
    transactions: Mapped[list[Transaction]] = relationship(
        secondary=transaction_categories, back_populates="categories"
    )
