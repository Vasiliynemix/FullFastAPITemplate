"""
Инициализация Sentry (error tracking).

No-op без SENTRY_DSN — Sentry включается только когда DSN задан. sentry-sdk сам
автоматически инструментирует FastAPI/Starlette/asyncio/Redis/SQLAlchemy, если они
установлены (default integrations), поэтому отдельно подключать их не нужно.

Вызывается как можно раньше: в create_app() (веб) и в worker._main() (воркер).
Непойманные исключения уходят в Sentry из глобального хендлера (см. handlers.py).
"""

from __future__ import annotations

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("sentry")


def init_sentry() -> bool:
    """Инициализировать Sentry, если задан DSN. Возвращает True, если включён."""
    if not settings.sentry_dsn:
        return False

    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment.value,
        release=settings.sentry_release or None,
        # Доля транзакций для performance-трейсинга (0 = только ошибки, без APM)
        traces_sample_rate=settings.sentry_traces_sample_rate,
        # PII (ip/куки/тело) по умолчанию НЕ отправляем — безопасный дефолт
        send_default_pii=False,
    )
    logger.info("sentry_initialized", environment=settings.environment.value)
    return True
