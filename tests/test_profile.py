"""upsert_profile: создание профиля (one-to-one), частичное обновление, 404 на чужом id."""

from __future__ import annotations

import uuid

import pytest

from app.db.uow import UnitOfWork
from app.exceptions.base import NotFoundError
from app.models.user import User
from app.schemas.account import ProfileUpsert
from app.services.user import UserService

pytestmark = pytest.mark.asyncio


async def _seed_user(sessionmaker) -> uuid.UUID:
    async with sessionmaker() as s:
        u = User(email=f"prof_{uuid.uuid4().hex}@example.com", full_name="Prof")
        s.add(u)
        await s.commit()
        return u.id


def _svc(sessionmaker, fake_cache) -> UserService:
    return UserService(uow=UnitOfWork(sessionmaker), cache=fake_cache)


async def test_upsert_creates_profile_when_absent(sessionmaker, fake_cache):
    uid = await _seed_user(sessionmaker)
    svc = _svc(sessionmaker, fake_cache)

    prof = await svc.upsert_profile(uid, ProfileUpsert(bio="hello", avatar_url="http://a/1.png"))

    assert prof.bio == "hello"
    assert prof.avatar_url == "http://a/1.png"


async def test_upsert_updates_only_passed_fields(sessionmaker, fake_cache):
    uid = await _seed_user(sessionmaker)
    svc = _svc(sessionmaker, fake_cache)
    await svc.upsert_profile(uid, ProfileUpsert(bio="first", avatar_url="http://a/1.png"))

    # передаём ТОЛЬКО bio -> avatar_url должен сохраниться (PUT-партиал, exclude_unset)
    prof = await svc.upsert_profile(uid, ProfileUpsert(bio="second"))

    assert prof.bio == "second"
    assert prof.avatar_url == "http://a/1.png"


async def test_upsert_unknown_user_raises_404(sessionmaker, fake_cache):
    with pytest.raises(NotFoundError):
        await _svc(sessionmaker, fake_cache).upsert_profile(uuid.uuid4(), ProfileUpsert(bio="x"))
