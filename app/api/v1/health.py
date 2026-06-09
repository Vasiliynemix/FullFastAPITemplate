"""
Health-чеки: liveness и readiness.

* /health/live  — процесс жив (для restart-политик), без обращения к зависимостям.
* /health/ready — проверяет ВКЛЮЧЁННЫЕ компоненты (postgres/redis всегда, broker/storage —
  если включены флагами). Для LB/k8s readiness: при недоступности любого компонента
  отдаёт HTTP 503 (узел убирают из ротации).

Эндпоинт открыт (не за rate limiter'ом и не за глобальным ключом), поэтому снимок конфига
(config) по умолчанию отдаётся ТОЛЬКО не в проде — он раскрывает защитную конфигурацию.
В проде включается флагом HEALTH_EXPOSE_CONFIG. Секреты и тип брокера/хранилища не отдаём.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.broker.factory import get_broker
from app.cache.redis_cache import get_redis
from app.core.config import settings
from app.db.session import get_sessionmaker
from app.schemas.response import SuccessResponse, success
from app.storage.factory import get_storage

router = APIRouter()


@router.get("/health/live", response_model=SuccessResponse[dict])
async def liveness() -> SuccessResponse[dict]:
    return success({"status": "alive"})


def _active_config() -> dict:
    """
    Снимок активной конфигурации — без секретов И без фингерпринта стека (тип брокера/
    хранилища не раскрываем). По умолчанию виден только не в проде (см. readiness):
    раскрывать защитную конфигурацию (rate_limit/режимы авторизации) на ОТКРЫТОЙ ручке —
    это подсказка атакующему, поэтому в проде скрыто (флаг health_expose_config).
    """
    return {
        "environment": settings.environment.value,
        "auth": {
            "jwt": settings.auth_jwt_enabled,
            "global_api_key": settings.global_api_key_enabled,
            "token_transport": settings.auth_token_transport.value,
            "validate_session": settings.auth_validate_session,
        },
        "broker": {"enabled": settings.broker_enabled},
        "storage": {"enabled": settings.storage_enabled},
        "outbox": {"enabled": settings.outbox_enabled},
        "rate_limit": settings.rate_limit_enabled,
        "sentry": bool(settings.sentry_dsn),
        "docs": settings.docs_enabled,
    }


@router.get("/health/ready", response_model=SuccessResponse[dict])
async def readiness(response: Response) -> SuccessResponse[dict]:
    checks: dict[str, str] = {}

    # Postgres (базовый компонент)
    try:
        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception:
        checks["postgres"] = "down"

    # Redis (базовый компонент)
    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "down"

    # Брокер — только если включён
    if settings.broker_enabled:
        try:
            checks["broker"] = "ok" if await get_broker().healthcheck() else "down"
        except Exception:
            checks["broker"] = "down"

    # Объектное хранилище — только если включено
    if settings.storage_enabled:
        try:
            checks["storage"] = "ok" if await get_storage().healthcheck() else "down"
        except Exception:
            checks["storage"] = "down"

    healthy = all(v == "ok" for v in checks.values())
    if not healthy:
        response.status_code = 503  # readiness провален -> убрать узел из ротации LB

    data: dict = {"status": "ok" if healthy else "degraded", "checks": checks}
    # Конфиг — только не в проде (или если явно включён флагом): /ready открыт публично,
    # а конфиг раскрывает защитную конфигурацию.
    if not settings.is_prod or settings.health_expose_config:
        data["config"] = _active_config()
    return success(data)
