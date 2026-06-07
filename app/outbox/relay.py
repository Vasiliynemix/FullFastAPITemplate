"""
Релей outbox -> брокер.

Раз в N секунд (планировщик воркера) забирает неопубликованные строки и публикует их
в брокер. Сделано в ТРИ короткие фазы, чтобы НЕ держать соединение БД во время сетевых
publish'ей в брокер (тот самый урок: медленный I/O нельзя держать внутри транзакции):

  1) короткая транзакция: выбрать пачку неопубликованных и снять данные;
  2) publish в брокер (без БД-транзакции);
  3) короткая транзакция: пометить published_at успешно отправленные.

Семантика доставки — at-least-once: если упадём между (2) и (3), строки переотправятся
на следующем тике. Поэтому консьюмеры должны быть идемпотентны.

Один экземпляр (scheduler single_instance + Redis-лок) => без двойной публикации.

ВАЖНО: outbox осмыслен с ДОЛГОВЕЧНЫМ брокером (Kafka/RabbitMQ), где релей (воркер) и
консьюмеры — разные процессы. С in-memory брокером всё живёт в одном процессе.
"""

from __future__ import annotations

from collections.abc import Callable

from app.broker.base import AbstractBroker, Message
from app.core.logging import get_logger
from app.db.uow import UnitOfWork

logger = get_logger("outbox.relay")


class OutboxRelay:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        broker: AbstractBroker,
        *,
        batch_size: int = 100,
    ) -> None:
        self._uow_factory = uow_factory
        self._broker = broker
        self._batch_size = batch_size

    async def run_once(self) -> int:
        """Один проход релея. Возвращает число опубликованных сообщений."""
        # (1) выбрать пачку и сразу снять данные — сессия закроется при выходе из with
        async with self._uow_factory() as uow:
            rows = await uow.outbox.fetch_unpublished(limit=self._batch_size)
            batch = [(r.id, r.topic, r.payload, r.key) for r in rows]
        if not batch:
            return 0

        # (2) publish в брокер БЕЗ открытой транзакции БД
        sent_ids = []
        for mid, topic, payload, key in batch:
            try:
                await self._broker.publish(Message(topic=topic, payload=payload, key=key))
                sent_ids.append(mid)
            except Exception:
                # Не помечаем -> строка переотправится на следующем тике
                logger.error(
                    "outbox_publish_failed", topic=topic, message_id=str(mid), exc_info=True
                )

        # (3) пометить отправленные
        if sent_ids:
            async with self._uow_factory() as uow:
                await uow.outbox.mark_published(sent_ids)
                await uow.commit()

        logger.info("outbox_relayed", sent=len(sent_ids), batch=len(batch))
        return len(sent_ids)
