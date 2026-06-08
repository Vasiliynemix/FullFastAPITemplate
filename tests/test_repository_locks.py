"""
Пессимистичные блокировки в репозитории (for_update / skip_locked / nowait).

* SQL-генерация проверяется на диалекте PostgreSQL (без поднятия БД).
* Функциональная проверка — на SQLite: диалект молча игнорирует FOR UPDATE, поэтому
  вызовы с флагом просто отрабатывают и возвращают данные (без ошибок).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models.user import User
from app.repositories.base import BaseRepository
from app.repositories.user import UserRepository


def _pg_sql(for_update: bool, nowait: bool, skip_locked: bool) -> str:
    stmt = BaseRepository._lock(
        select(User), for_update=for_update, nowait=nowait, skip_locked=skip_locked
    )
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_for_update_renders():
    assert "FOR UPDATE" in _pg_sql(True, False, False)


def test_skip_locked_renders():
    sql = _pg_sql(True, False, True)
    assert "FOR UPDATE" in sql and "SKIP LOCKED" in sql


def test_nowait_renders():
    sql = _pg_sql(True, True, False)
    assert "FOR UPDATE" in sql and "NOWAIT" in sql


def test_no_lock_by_default():
    assert "FOR UPDATE" not in _pg_sql(False, False, False)


@pytest.mark.asyncio
async def test_getters_work_with_for_update_on_sqlite(session):
    repo = UserRepository(session)
    user = await repo.add(User(email="lock@example.com", full_name="Lock"))
    await session.commit()

    # все геттеры с флагом отрабатывают и возвращают корректные данные
    got = await repo.get(user.id, for_update=True)
    assert got is not None and got.id == user.id

    by = await repo.get_by(email="lock@example.com", for_update=True, nowait=True)
    assert by is not None and by.id == user.id

    rows = await repo.list(for_update=True, skip_locked=True)
    assert len(rows) == 1 and rows[0].id == user.id
