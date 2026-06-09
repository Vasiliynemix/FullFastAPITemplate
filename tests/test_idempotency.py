"""Хелпер idempotent(): replay, конфликт in-flight (409), отпускание ключа при ошибке."""

from __future__ import annotations

import pytest

from app.exceptions.base import ConflictError
from app.idempotency.runner import idempotent
from app.idempotency.store import IdempotencyStore
from app.schemas.response import SuccessResponse, success


@pytest.fixture
def store(monkeypatch, fake_redis):
    """Стор на fakeredis, подменяем им синглтон, который берёт idempotent()."""
    s = IdempotencyStore(fake_redis)
    monkeypatch.setattr("app.idempotency.runner.get_idempotency_store", lambda: s)
    return s


async def test_replay_returns_cached_without_reexecuting(store):
    calls = {"n": 0}

    async def produce() -> SuccessResponse[dict]:
        calls["n"] += 1
        return success({"value": calls["n"]})

    r1 = await idempotent("k1", SuccessResponse[dict], produce)
    r2 = await idempotent("k1", SuccessResponse[dict], produce)

    assert calls["n"] == 1  # produce выполнен ровно один раз
    assert r1.data == r2.data == {"value": 1}  # повтор вернул тот же ответ


async def test_in_progress_returns_conflict(store):
    # эмулируем «параллельный запрос ещё выполняется»: ключ занят, ответа ещё нет
    await store.try_acquire("k2")

    async def produce() -> SuccessResponse[dict]:
        return success({"value": 1})

    with pytest.raises(ConflictError):
        await idempotent("k2", SuccessResponse[dict], produce)


async def test_no_key_executes_every_time(store):
    calls = {"n": 0}

    async def produce() -> SuccessResponse[dict]:
        calls["n"] += 1
        return success({"value": calls["n"]})

    await idempotent(None, SuccessResponse[dict], produce)
    await idempotent(None, SuccessResponse[dict], produce)

    assert calls["n"] == 2  # без ключа идемпотентности нет


async def test_error_releases_key_for_retry(store):
    async def boom() -> SuccessResponse[dict]:
        raise RuntimeError("fail")

    with pytest.raises(RuntimeError):
        await idempotent("k3", SuccessResponse[dict], boom)

    # ключ отпущен -> повтор НЕ упирается в 409 и выполняется
    async def ok() -> SuccessResponse[dict]:
        return success({"value": 42})

    r = await idempotent("k3", SuccessResponse[dict], ok)
    assert r.data == {"value": 42}
