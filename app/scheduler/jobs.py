"""
Периодические задачи воркера. Регистрируются в register_jobs().

Примеры показывают типовые сценарии: «живость» воркера и периодический опрос
внешнего сервиса. Добавить свою задачу = написать async-функцию + строку в register_jobs.
"""

from __future__ import annotations

import datetime

from app.broker.factory import get_broker
from app.cache.redis_cache import get_redis
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.db.uow import UnitOfWork
from app.outbox.relay import OutboxRelay
from app.scheduler.scheduler import Scheduler

logger = get_logger("jobs")


async def heartbeat() -> None:
    """Пример: отметка «воркер жив» в Redis (TTL) — удобно для мониторинга/healthcheck."""
    now = datetime.datetime.now(tz=datetime.UTC).isoformat()
    await get_redis().set("worker:heartbeat", now, ex=120)
    logger.info("heartbeat")


async def poll_external_service() -> None:
    """
    Пример: раз в час сходить во внешний сервис и что-то сделать.

    Здесь вызываешь свой клиент/сервис (app/clients, app/services) — внешний вызов идёт
    в отдельном процессе, не в веб-воркерах. Например:
        client = get_messages_client()
        ... = await client.get_status(task_id)
    Сейчас — заглушка-демо.
    """
    logger.info("poll_external_service_stub")


async def daily_report() -> None:
    """Пример cron-задачи: ежедневный отчёт/обслуживание (тут — заглушка)."""
    logger.info("daily_report_stub")


async def relay_outbox() -> None:
    """Опубликовать накопившиеся outbox-события в брокер (transactional outbox)."""
    relay = OutboxRelay(
        lambda: UnitOfWork(get_sessionmaker()),
        get_broker(),
        batch_size=settings.outbox_batch_size,
    )
    await relay.run_once()


async def cleanup_outbox() -> None:
    """Удалить давно опубликованные outbox-строки (retention)."""
    cutoff = datetime.datetime.now(tz=datetime.UTC).replace(tzinfo=None) - datetime.timedelta(
        days=settings.outbox_retention_days
    )
    async with UnitOfWork(get_sessionmaker()) as uow:
        deleted = await uow.outbox.delete_published_before(cutoff)
        await uow.commit()
    logger.info("outbox_cleanup", deleted=deleted)


def register_jobs(scheduler: Scheduler) -> None:
    """Все периодические задачи в одном месте."""
    # Интервал: «живость» — часто и сразу на старте
    scheduler.add("heartbeat", heartbeat, interval=30, run_on_start=True)
    # Интервал: опрос внешнего сервиса — раз в час, с защитой от двойного запуска
    scheduler.add("poll_external", poll_external_service, interval=3600, single_instance=True)
    # Cron: каждый день в 03:00 UTC (минута час день месяц день_недели)
    scheduler.add_cron("daily_report", daily_report, "0 3 * * *", single_instance=True)

    # Outbox relay: публикуем накопленные события. single_instance => без двойной отправки.
    # Релею нужен брокер — без него не запускаем (события некуда публиковать).
    if settings.outbox_enabled and settings.broker_enabled:
        scheduler.add(
            "outbox_relay",
            relay_outbox,
            interval=settings.outbox_relay_interval,
            run_on_start=True,
            single_instance=True,
        )
        # Чистка опубликованных строк — ночью
        scheduler.add_cron("outbox_cleanup", cleanup_outbox, "0 4 * * *", single_instance=True)
