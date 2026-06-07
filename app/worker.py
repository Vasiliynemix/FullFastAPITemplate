"""
Воркер периодических задач — ОТДЕЛЬНЫЙ процесс (не gunicorn, не веб).

Запуск: `python -m app.worker` (или `uv run worker`). В Docker — тот же образ, что и
backend, но другая команда; один экземпляр (без replicas).

Сам поднимает ресурсы (Redis/брокер/пул БД), регистрирует задачи, крутит планировщик
и аккуратно гасит всё по SIGTERM/SIGINT (graceful shutdown).
"""

from __future__ import annotations

import asyncio
import signal

from app.broker.factory import get_broker, reset_broker
from app.cache.redis_cache import close_redis, get_redis
from app.clients.messages import close_messages_client
from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.core.observability import init_sentry
from app.db.session import dispose_engine
from app.scheduler.jobs import register_jobs
from app.scheduler.scheduler import Scheduler
from app.storage.factory import get_storage, reset_storage

logger = get_logger("worker")


async def _main() -> None:
    setup_logging()
    init_sentry()  # no-op без SENTRY_DSN
    logger.info("worker_startup")

    # Прогрев ресурсов (как в lifespan веб-приложения, но без FastAPI)
    await get_redis().ping()
    broker = None
    if settings.broker_enabled:
        broker = get_broker()
        await broker.connect()
    storage = None
    if settings.storage_enabled:
        storage = get_storage()
        await storage.connect()  # на случай задач, пишущих в хранилище

    scheduler = Scheduler()
    register_jobs(scheduler)

    # Грейсфул-стоп по сигналам контейнера/ОС
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, scheduler.stop)

    try:
        await scheduler.run()  # блокируется до stop()
    finally:
        logger.info("worker_shutdown")
        if broker is not None:
            await broker.disconnect()
            reset_broker()
        if storage is not None:
            await storage.close()
            reset_storage()
        await close_messages_client()
        await close_redis()
        await dispose_engine()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
