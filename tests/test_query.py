"""
Универсальные фильтры/сортировка/поиск (app/db/query.py + BaseRepository.paginate).

Юнит-тесты валидации/санитайза + интеграция paginate на SQLite (ILIKE-фолбэк поиска;
триграммный fuzzy проверяется на живом Postgres).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.config import AcquirerName
from app.db.query import apply_filters, apply_sort, sanitize_q
from app.db.uow import UnitOfWork
from app.exceptions.base import BadRequestError
from app.models.user import User


# ---------- sanitize_q (защита q) ----------
def test_sanitize_q_strips_html():
    out = sanitize_q("<script>alert(1)</script>вася")
    assert "<" not in out and ">" not in out and "вася" in out


def test_sanitize_q_removes_control_and_limits():
    assert "\x00" not in sanitize_q("ab\x00c")
    assert sanitize_q("a" * 500, max_len=10) == "a" * 10


# ---------- валидация фильтров/сортировки ----------
def test_filter_unknown_field_raises():
    with pytest.raises(BadRequestError, match="Unknown filter field"):
        apply_filters(select(User), User, {"nope__eq": "x"})


def test_filter_unknown_operator_raises():
    with pytest.raises(BadRequestError, match="operator"):
        apply_filters(select(User), User, {"email__zzz": "a"})


def test_filter_bad_value_raises():
    with pytest.raises(BadRequestError, match="Bad value"):
        apply_filters(select(User), User, {"created_at__ge": "not-a-date"})


def test_sort_unknown_field_raises():
    with pytest.raises(BadRequestError, match="Unknown sort field"):
        apply_sort(select(User), User, "nope")


# ---------- paginate (интеграция, SQLite) ----------
async def _seed(sessionmaker):
    async with sessionmaker() as s:
        s.add_all(
            [
                User(email="vasya@e.com", full_name="Вася", is_active=True, role="admin"),
                User(email="petya@e.com", full_name="Петя", is_active=True, role="user"),
                User(email="anya@e.com", full_name="Аня", is_active=False, role="user"),
            ]
        )
        await s.commit()


async def test_paginate_filter(sessionmaker):
    await _seed(sessionmaker)
    async with UnitOfWork(sessionmaker) as uow:
        items, total = await uow.users.paginate(filters={"is_active__eq": "true"})
        assert total == 2
        assert {u.full_name for u in items} == {"Вася", "Петя"}


async def test_paginate_in_operator(sessionmaker):
    await _seed(sessionmaker)
    async with UnitOfWork(sessionmaker) as uow:
        items, total = await uow.users.paginate(filters={"role__in": "admin,manager"})
        assert total == 1 and items[0].full_name == "Вася"


async def test_paginate_sort(sessionmaker):
    await _seed(sessionmaker)
    async with UnitOfWork(sessionmaker) as uow:
        asc = await uow.users.paginate(sort="full_name")
        assert [u.full_name for u in asc[0]] == ["Аня", "Вася", "Петя"]
        desc = await uow.users.paginate(sort="-full_name")
        assert desc[0][0].full_name == "Петя"


async def test_paginate_search_and_pagination(sessionmaker):
    await _seed(sessionmaker)
    async with UnitOfWork(sessionmaker) as uow:
        # SQLite ILIKE сворачивает регистр только для ASCII -> ищем по email (vasya).
        # Кириллический fuzzy («всая»->«вася») — фича Postgres pg_trgm, см. живой тест.
        items, total = await uow.users.paginate(q="vasya")
        assert total == 1 and items[0].full_name == "Вася"
        page1, total = await uow.users.paginate(per_page=2, page=1)
        assert len(page1) == 2 and total == 3


async def test_paginate_with_eager_load(sessionmaker):
    from sqlalchemy.orm import selectinload

    from app.models.account import Account, Transaction

    async with sessionmaker() as s:
        u = User(email="p@example.com", full_name="P")
        s.add(u)
        await s.flush()
        acc = Account(user_id=u.id, name="a")
        acc.transactions.append(
            Transaction(amount=10, kind="deposit", acquirer=AcquirerName.MEMORY)
        )
        s.add(acc)
        await s.commit()

    # paginate со связями: transactions грузятся заранее (иначе MissingGreenlet вне сессии)
    async with UnitOfWork(sessionmaker) as uow:
        items, total = await uow.accounts.paginate(options=[selectinload(Account.transactions)])
    assert total == 1
    assert len(items[0].transactions) == 1 and items[0].transactions[0].amount == 10
