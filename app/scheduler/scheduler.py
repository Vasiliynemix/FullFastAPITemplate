"""
Лёгкий async-планировщик периодических задач.

Поддерживает ДВА вида расписания, одинаково просто добавляются:
* интервал — `scheduler.add(name, func, interval=3600)`  (раз в N секунд от старта);
* cron     — `scheduler.add_cron(name, func, "0 3 * * *")`  (каждый день в 03:00 UTC).

Особенности:
* graceful shutdown: на остановку (SIGTERM) задачи завершаются, сон прерывается сразу;
* ошибка одной задачи НЕ роняет планировщик (логируется, цикл продолжается);
* single_instance: задача берёт Redis-лок на время выполнения — защита от пересечения
  запусков и от случайного запуска воркера в нескольких экземплярах.

Cron-выражения трактуются в UTC. Поля стандартные: «минута час день месяц день_недели».
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter

from croniter import croniter

from app.core.logging import get_logger

logger = get_logger("scheduler")

JobFunc = Callable[[], Awaitable[None]]


@dataclass(slots=True)
class Job:
    name: str
    func: JobFunc
    interval: float | None = None  # либо интервал в секундах...
    cron: str | None = None  # ...либо cron-выражение (UTC)
    run_on_start: bool = False  # выполнить сразу на старте
    single_instance: bool = False  # брать Redis-лок (один запуск одновременно)
    lock_ttl: int = 300  # TTL лока (страховка, если процесс умрёт в задаче)


class Scheduler:
    def __init__(self) -> None:
        self._jobs: list[Job] = []
        self._stop = asyncio.Event()

    def add(
        self,
        name: str,
        func: JobFunc,
        *,
        interval: float,
        run_on_start: bool = False,
        single_instance: bool = False,
        lock_ttl: int = 300,
    ) -> None:
        """Интервальная задача: запуск раз в `interval` секунд."""
        self._jobs.append(
            Job(
                name=name,
                func=func,
                interval=interval,
                run_on_start=run_on_start,
                single_instance=single_instance,
                lock_ttl=lock_ttl,
            )
        )

    def add_cron(
        self,
        name: str,
        func: JobFunc,
        cron: str,
        *,
        run_on_start: bool = False,
        single_instance: bool = False,
        lock_ttl: int = 300,
    ) -> None:
        """Задача по cron-расписанию (UTC), напр. "0 3 * * *" — каждый день в 03:00."""
        if not croniter.is_valid(cron):
            raise ValueError(f"invalid cron expression: {cron!r}")
        self._jobs.append(
            Job(
                name=name,
                func=func,
                cron=cron,
                run_on_start=run_on_start,
                single_instance=single_instance,
                lock_ttl=lock_ttl,
            )
        )

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        if not self._jobs:
            logger.warning("scheduler_no_jobs")
            return
        tasks = [asyncio.create_task(self._loop(j), name=f"job:{j.name}") for j in self._jobs]
        logger.info("scheduler_started", jobs=[j.name for j in self._jobs])
        await self._stop.wait()
        logger.info("scheduler_stopping")
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("scheduler_stopped")

    async def _loop(self, job: Job) -> None:
        if job.run_on_start:
            await self._run(job)
        while not self._stop.is_set():
            delay = self._next_delay(job)
            # сон с прерыванием: stop -> выходим сразу; таймаут -> пора запускать
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            if self._stop.is_set():
                break
            await self._run(job)

    def _next_delay(self, job: Job) -> float:
        """Сколько секунд спать до следующего запуска."""
        if job.cron is not None:
            now = datetime.datetime.now(tz=datetime.UTC)
            nxt = croniter(job.cron, now).get_next(datetime.datetime)
            return max(0.0, (nxt - now).total_seconds())
        return job.interval if job.interval is not None else 0.0

    async def _run(self, job: Job) -> None:
        if job.single_instance and not await self._acquire(job):
            logger.info("job_skipped_locked", job=job.name)
            return
        start = perf_counter()
        try:
            await job.func()
            elapsed = round((perf_counter() - start) * 1000, 2)
            logger.info("job_done", job=job.name, latency_ms=elapsed)
        except Exception:
            # Ошибка задачи не должна ронять планировщик
            logger.error("job_failed", job=job.name, exc_info=True)
        finally:
            if job.single_instance:
                await self._release(job)

    async def _acquire(self, job: Job) -> bool:
        from app.cache.redis_cache import get_redis

        return bool(
            await get_redis().set(f"scheduler:lock:{job.name}", b"1", nx=True, ex=job.lock_ttl)
        )

    async def _release(self, job: Job) -> None:
        from app.cache.redis_cache import get_redis

        await get_redis().delete(f"scheduler:lock:{job.name}")
