"""
Консьюмеры доменного события UserCreated.

Демонстрируют FAN-OUT: на ОДНО событие подписано НЕСКОЛЬКО независимых обработчиков
(аудит + welcome). Каждый делает своё дело и изолирован от других (упал один — другие
отработают). Событие публикует UserService.create при регистрации пользователя.

ВАЖНО про backend брокера: настоящий fan-out (каждый handler получает КАЖДОЕ событие)
работает «из коробки» с in-memory брокером. Для Kafka/RabbitMQ, чтобы оба обработчика
получали все сообщения (а не делили их как конкурирующие потребители), каждому нужна
СВОЯ consumer-group / отдельная очередь — это расширение брокер-абстракции.
"""

from __future__ import annotations

from app.cache.redis_cache import get_redis_cache
from app.core.logging import get_logger
from app.services.user import UserCreated

logger = get_logger("consumer.users")


async def handle_user_created_audit(event: UserCreated) -> None:
    """
    Пример 1 — аналитика/аудит: считаем регистрации (метрика в Redis).
    Показывает, как консьюмер пользуется инфраструктурой (кэш/Redis).
    """
    cache = get_redis_cache()
    total = await cache.incr("stats:users_created")
    logger.info("user_created_audit", user_id=event.id, total_signups=total)


async def handle_user_created_welcome(event: UserCreated) -> None:
    """
    Пример 2 — welcome-флоу: место для приветственного письма/бонуса/онбординга.
    Тяжёлый внешний вызов идёт ВНЕ request lifecycle, не блокирует регистрацию.
    """
    # Здесь обычно: отправить welcome-email через email-клиент, начислить бонус,
    # завести профиль в CRM и т.п. Можно и опубликовать новое событие (цепочка).
    logger.info("user_created_welcome", user_id=event.id, email=event.email)
