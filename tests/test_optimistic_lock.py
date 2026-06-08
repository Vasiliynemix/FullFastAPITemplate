"""
Оптимистичная блокировка (VersionedMixin): инкремент версии, конфликт -> StaleDataError,
а в сервисном слое -> ConflictError (409).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm.exc import StaleDataError

from app.db.uow import UnitOfWork
from app.exceptions.base import ConflictError
from app.models.base import Base
from app.models.user import User
from app.schemas.user import UserUpdate
from app.services.user import UserService

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def file_sm(tmp_path):
    # Файловый SQLite => два независимых соединения (нужно для настоящей гонки).
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/o.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(eng, expire_on_commit=False)
    await eng.dispose()


async def test_version_increments_on_update(session):
    user = User(email="v@example.com", full_name="V")
    session.add(user)
    await session.commit()
    assert user.version_id == 1  # ORM проставляет начальную версию на INSERT

    user.full_name = "V2"
    await session.commit()
    assert user.version_id == 2  # и инкрементит на каждый UPDATE


async def test_concurrent_update_raises_stale(file_sm):
    async with file_sm() as s:
        u = User(email="c@example.com", full_name="C")
        s.add(u)
        await s.commit()
        uid = u.id

    # Две независимые сессии читают ОДНУ строку (обе version=1)
    async with file_sm() as sa, file_sm() as sb:
        ua = await sa.get(User, uid)
        ub = await sb.get(User, uid)

        ua.full_name = "A"
        await sa.commit()  # version 1 -> 2

        ub.full_name = "B"
        with pytest.raises(StaleDataError):
            await sb.commit()  # ждёт version=1, а там уже 2 -> 0 строк -> конфликт


async def test_service_maps_stale_to_conflict(sessionmaker, fake_cache, monkeypatch):
    async with sessionmaker() as s:
        u = User(email="m@example.com", full_name="M")
        s.add(u)
        await s.commit()
        uid = u.id

    # Имитируем проигранную гонку: commit бросает StaleDataError
    async def _boom(self) -> None:
        raise StaleDataError("simulated lost update")

    monkeypatch.setattr(UnitOfWork, "commit", _boom)

    svc = UserService(uow=UnitOfWork(sessionmaker), cache=fake_cache)
    with pytest.raises(ConflictError, match="concurrently"):
        await svc.update(uid, UserUpdate(full_name="X"))
