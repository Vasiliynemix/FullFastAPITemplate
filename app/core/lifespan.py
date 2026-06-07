"""
Жизненный цикл приложения (startup / graceful shutdown).

Startup: настроить логирование, поднять брокер, прогреть пул Redis.
Shutdown: корректно закрыть брокер, Redis и пул соединений БД — это и есть
graceful shutdown (вместе с таймаутами gunicorn/uvicorn новые запросы не теряются).

Движок БД создаётся лениво при первом запросе, но Redis/брокер инициализируем
заранее, чтобы первый запрос не платил за «холодный старт».
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.broker.events import EventBus
from app.broker.factory import get_broker, reset_broker
from app.cache.redis_cache import close_redis, get_redis
from app.clients.messages import close_messages_client
from app.consumers import register_consumers
from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.db.session import dispose_engine
from app.storage.factory import get_storage, reset_storage

logger = get_logger("lifespan")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    logger.info("startup_begin")

    # Прогрев Redis-пула
    await get_redis().ping()

    # Брокер + консьюмеры — только если включён (иначе не тянем лишнее)
    broker = None
    if settings.broker_enabled:
        broker = get_broker()
        await broker.connect()
        await register_consumers(EventBus(broker))

    # Объектное хранилище (S3) — только если включено
    storage = None
    if settings.storage_enabled:
        storage = get_storage()
        await storage.connect()

    logger.info("startup_complete")
    try:
        yield
    finally:
        logger.info("shutdown_begin")
        if broker is not None:
            await broker.disconnect()
            reset_broker()
        if storage is not None:
            await storage.close()
            reset_storage()
        await close_messages_client()  # закрываем пул внешнего HTTP-клиента
        await close_redis()
        await dispose_engine()
        logger.info("shutdown_complete")
