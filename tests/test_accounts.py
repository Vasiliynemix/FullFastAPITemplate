"""
Демо-домен «счета»: операции под FOR UPDATE + eager-load всех типов связей.

Позитивные тесты доказывают eager-load: model_validate в сервисе обращается к ORM-связям,
и если бы они не были загружены — упал бы MissingGreenlet. Отдельный тест показывает и
сам провал ленивой загрузки в async.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import MissingGreenlet
from sqlalchemy.orm.exc import DetachedInstanceError

from app.core.config import AcquirerName
from app.db.uow import UnitOfWork
from app.exceptions.base import ConflictError, NotFoundError
from app.models.account import Account, Category
from app.models.profile import Profile
from app.models.user import User
from app.services.account import AccountService

pytestmark = pytest.mark.asyncio


async def _seed_user(sessionmaker, *, with_profile: bool = False) -> uuid.UUID:
    async with sessionmaker() as s:
        u = User(email=f"acc_{uuid.uuid4().hex}@example.com", full_name="Acc")
        if with_profile:
            u.profile = Profile(bio="hi")  # one-to-one
        s.add(u)
        await s.commit()
        return u.id


async def _seed_category(sessionmaker, name: str) -> uuid.UUID:
    async with sessionmaker() as s:
        c = Category(name=name)
        s.add(c)
        await s.commit()
        return c.id


def _svc(sessionmaker) -> AccountService:
    return AccountService(uow=UnitOfWork(sessionmaker))


async def test_create_deposit_withdraw(sessionmaker):
    uid = await _seed_user(sessionmaker)
    svc = _svc(sessionmaker)
    acc = await svc.create_account(uid, "main")
    assert acc.balance == 0
    assert (await svc.deposit(acc.id, 500, AcquirerName.MEMORY)).balance == 500
    assert (await svc.withdraw(acc.id, 200, AcquirerName.MEMORY)).balance == 300


async def test_insufficient_funds(sessionmaker):
    uid = await _seed_user(sessionmaker)
    svc = _svc(sessionmaker)
    acc = await svc.create_account(uid, "main")
    await svc.deposit(acc.id, 100, AcquirerName.MEMORY)
    with pytest.raises(ConflictError, match="Insufficient"):
        await svc.withdraw(acc.id, 1000, AcquirerName.MEMORY)


async def test_get_account_eager_loads_transactions_and_categories(sessionmaker):
    uid = await _seed_user(sessionmaker)
    cat = await _seed_category(sessionmaker, "food")
    svc = _svc(sessionmaker)
    acc = await svc.create_account(uid, "main")
    await svc.deposit(acc.id, 300, AcquirerName.MEMORY, category_ids=[cat])  # many-to-many запись

    detail = await svc.get_account(acc.id)  # eager-load one-to-many + many-to-many
    assert detail.balance == 300
    assert len(detail.transactions) == 1
    assert detail.transactions[0].amount == 300
    assert [c.name for c in detail.transactions[0].categories] == ["food"]


async def test_user_overview_nested_eager_load(sessionmaker):
    uid = await _seed_user(sessionmaker, with_profile=True)
    svc = _svc(sessionmaker)
    acc = await svc.create_account(uid, "main")
    await svc.deposit(acc.id, 150, AcquirerName.MEMORY)

    ov = await svc.get_user_overview(uid)  # 1-1 profile + 1-many accounts + вложенные транзакции
    assert ov.profile is not None and ov.profile.bio == "hi"
    assert len(ov.accounts) == 1
    assert ov.accounts[0].transactions[0].amount == 150


async def test_missing_account_raises_not_found(sessionmaker):
    with pytest.raises(NotFoundError):
        await _svc(sessionmaker).get_account(uuid.uuid4())


async def test_create_and_list_category(sessionmaker):
    svc = _svc(sessionmaker)
    cat = await svc.create_category("food")
    assert cat.name == "food"
    assert [c.name for c in await svc.list_categories()] == ["food"]


async def test_duplicate_category_conflict(sessionmaker):
    svc = _svc(sessionmaker)
    await svc.create_category("food")
    with pytest.raises(ConflictError):
        await svc.create_category("food")


async def test_deposit_unknown_category_raises_404(sessionmaker):
    uid = await _seed_user(sessionmaker)
    svc = _svc(sessionmaker)
    acc = await svc.create_account(uid, "main")
    # несуществующая категория -> 404, а НЕ молчаливый пропуск
    with pytest.raises(NotFoundError, match="categories"):
        await svc.deposit(acc.id, 100, AcquirerName.MEMORY, category_ids=[uuid.uuid4()])


async def test_lazy_load_without_eager_fails(sessionmaker):
    # Демонстрация грабли: связь без eager-load -> обращение вне сессии падает
    uid = await _seed_user(sessionmaker)
    async with sessionmaker() as s:
        s.add(Account(user_id=uid, name="x"))
        await s.commit()
    async with UnitOfWork(sessionmaker) as uow:
        acc = await uow.accounts.get_by(name="x")  # БЕЗ options
    assert acc is not None
    with pytest.raises((MissingGreenlet, DetachedInstanceError)):
        _ = acc.transactions  # связь без eager + вне сессии -> ошибка (так и задумано)
