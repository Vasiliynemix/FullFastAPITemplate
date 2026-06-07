"""
Перформанс-ориентированные тесты (маркер `perf`).

Запуск только их:  pytest -m perf
Это не нагрузочное тестирование (для него — k6/locust против поднятого стенда),
а быстрые микробенчмарки/санити-проверки горячих путей в CI.
"""

from __future__ import annotations

import asyncio
import time

import pytest

pytestmark = pytest.mark.perf


@pytest.mark.asyncio
async def test_response_serialization_is_fast():
    """Сериализация единого ответа через orjson должна быть суб-миллисекундной."""
    from app.schemas.response import success

    payload = {"id": "x", "email": "a@b.c", "full_name": "Name", "is_active": True}
    resp = success(payload)

    start = time.perf_counter()
    for _ in range(10_000):
        resp.model_dump(mode="json")
    elapsed = time.perf_counter() - start

    # 10k сериализаций должны укладываться в разумные пределы (защита от регрессий)
    assert elapsed < 1.0, f"serialization too slow: {elapsed:.3f}s for 10k"


@pytest.mark.asyncio
async def test_concurrent_cache_access(fake_cache):
    """Параллельные обращения к кэшу не теряют данные (санити конкурентности)."""

    async def writer(i: int) -> None:
        await fake_cache.set(f"k{i}", {"v": i})

    await asyncio.gather(*(writer(i) for i in range(1000)))
    assert await fake_cache.get("k500") == {"v": 500}


@pytest.mark.asyncio
async def test_repository_bulk_insert(session):
    """Sanity: пакетная вставка 1000 строк проходит за приемлемое время."""
    from app.models.user import User
    from app.repositories.user import UserRepository

    repo = UserRepository(session)
    start = time.perf_counter()
    for i in range(1000):
        session.add(User(email=f"perf{i}@example.com", full_name=f"P{i}"))
    await session.commit()
    elapsed = time.perf_counter() - start

    assert await repo.count() == 1000
    assert elapsed < 5.0, f"bulk insert too slow: {elapsed:.3f}s"
