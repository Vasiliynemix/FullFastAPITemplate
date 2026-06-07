"""Юнит/интеграционные тесты репозитория поверх async SQLite."""

from __future__ import annotations

import uuid

import pytest

from app.models.user import User
from app.repositories.user import UserRepository

pytestmark = pytest.mark.asyncio


async def test_add_and_get(session):
    repo = UserRepository(session)
    user = await repo.add(User(email="a@example.com", full_name="Alice"))
    await session.commit()

    fetched = await repo.get(user.id)
    assert fetched is not None
    assert fetched.email == "a@example.com"


async def test_get_by_email(session):
    repo = UserRepository(session)
    await repo.add(User(email="b@example.com", full_name="Bob"))
    await session.commit()

    found = await repo.get_by_email("b@example.com")
    assert found is not None
    assert found.full_name == "Bob"


async def test_list_and_count(session):
    repo = UserRepository(session)
    for i in range(5):
        await repo.add(User(email=f"u{i}@example.com", full_name=f"User {i}"))
    await session.commit()

    items = await repo.list(limit=3, offset=0, order_by=User.email)
    assert len(items) == 3
    assert await repo.count() == 5


async def test_delete_by_id(session):
    repo = UserRepository(session)
    user = await repo.add(User(email="c@example.com", full_name="Carol"))
    await session.commit()

    deleted = await repo.delete_by_id(user.id)
    await session.commit()
    assert deleted == 1
    assert await repo.get(user.id) is None


async def test_get_missing_returns_none(session):
    repo = UserRepository(session)
    assert await repo.get(uuid.uuid4()) is None
