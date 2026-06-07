"""
Фабрика брокера + общий singleton.

Выбирает реализацию по BROKER_TYPE. Конкретные клиенты импортируются лениво —
чтобы не тянуть aiokafka/aio-pika, когда они не нужны (например в тестах с memory).
"""

from __future__ import annotations

from app.broker.base import AbstractBroker
from app.broker.memory import InMemoryBroker
from app.core.config import BrokerType, settings

_broker: AbstractBroker | None = None


def build_broker() -> AbstractBroker:
    match settings.broker_type:
        case BrokerType.KAFKA:
            from app.broker.kafka import KafkaBroker

            return KafkaBroker(settings.broker_url)
        case BrokerType.RABBITMQ:
            from app.broker.rabbitmq import RabbitMQBroker

            return RabbitMQBroker(settings.broker_url)
        case _:
            return InMemoryBroker()


def get_broker() -> AbstractBroker:
    """Singleton брокера на процесс. Инициализируется в lifespan."""
    global _broker
    if _broker is None:
        _broker = build_broker()
    return _broker


def reset_broker() -> None:
    global _broker
    _broker = None
