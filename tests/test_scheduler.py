"""Тесты планировщика периодических задач."""

from __future__ import annotations

import asyncio

import pytest

from app.scheduler.scheduler import Scheduler

pytestmark = pytest.mark.asyncio


async def test_run_on_start_then_stop():
    calls: list[int] = []

    async def job() -> None:
        calls.append(1)

    s = Scheduler()
    s.add("j", job, interval=100, run_on_start=True)
    task = asyncio.create_task(s.run())
    await asyncio.sleep(0.05)  # дать run_on_start выполниться
    s.stop()
    await asyncio.wait_for(task, timeout=2)

    assert calls == [1]  # один запуск на старте, интервал ещё не наступил


async def test_runs_repeatedly_on_interval():
    calls: list[int] = []

    async def job() -> None:
        calls.append(1)

    s = Scheduler()
    s.add("j", job, interval=0.02)  # быстрый интервал для теста
    task = asyncio.create_task(s.run())
    await asyncio.sleep(0.1)  # успеет несколько раз
    s.stop()
    await asyncio.wait_for(task, timeout=2)

    assert len(calls) >= 2  # сработал несколько раз


async def test_job_error_does_not_crash_scheduler():
    ok: list[int] = []

    async def bad() -> None:
        raise ValueError("boom")

    async def good() -> None:
        ok.append(1)

    s = Scheduler()
    s.add("bad", bad, interval=0.02, run_on_start=True)
    s.add("good", good, interval=0.02, run_on_start=True)
    task = asyncio.create_task(s.run())
    await asyncio.sleep(0.1)
    s.stop()
    await asyncio.wait_for(task, timeout=2)

    # упавшая задача не помешала второй работать
    assert len(ok) >= 1


async def test_add_cron_validates_expression():
    s = Scheduler()

    async def job() -> None: ...

    s.add_cron("ok", job, "*/5 * * * *")  # валидный — не падает
    with pytest.raises(ValueError, match="invalid cron"):
        s.add_cron("bad", job, "не cron")


async def test_cron_next_delay_is_reasonable():
    s = Scheduler()

    async def job() -> None: ...

    s.add_cron("every_minute", job, "* * * * *")  # каждую минуту
    job_obj = s._jobs[0]
    delay = s._next_delay(job_obj)
    # до следующей минуты — больше 0 и не больше 60 секунд
    assert 0 < delay <= 60


async def test_cron_runs_on_start():
    calls: list[int] = []

    async def job() -> None:
        calls.append(1)

    s = Scheduler()
    # cron редкий (раз в день), но run_on_start -> сработает сразу
    s.add_cron("daily", job, "0 3 * * *", run_on_start=True)
    task = asyncio.create_task(s.run())
    await asyncio.sleep(0.05)
    s.stop()
    await asyncio.wait_for(task, timeout=2)

    assert calls == [1]


async def test_single_instance_lock_skips_when_held(fake_redis, monkeypatch):
    monkeypatch.setattr("app.cache.redis_cache.get_redis", lambda: fake_redis)
    runs: list[int] = []

    async def job() -> None:
        runs.append(1)

    s = Scheduler()
    s.add("locked", job, interval=100, run_on_start=True, single_instance=True)

    # лок уже занят «другим экземпляром» -> задача должна пропуститься
    await fake_redis.set("scheduler:lock:locked", b"1", nx=True, ex=100)

    task = asyncio.create_task(s.run())
    await asyncio.sleep(0.05)
    s.stop()
    await asyncio.wait_for(task, timeout=2)

    assert runs == []  # пропущена, т.к. лок занят
