"""
Health-чеки: liveness и readiness.

* /health/live — процесс жив (для restart-политик).
* /health/ready — зависимости (Postgres, Redis) доступны (для LB/k8s readiness).
Readiness делает дешёвые пинги; не лимитируется rate limiter'ом.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.cache.redis_cache import get_redis
from app.db.session import get_sessionmaker
from app.schemas.response import SuccessResponse, success

router = APIRouter()


@router.get("/health/live", response_model=SuccessResponse[dict])
async def liveness() -> SuccessResponse[dict]:
    return success({"status": "alive"})


@router.get("/health/ready", response_model=SuccessResponse[dict])
async def readiness() -> SuccessResponse[dict]:
    checks: dict[str, str] = {}

    # Postgres
    try:
        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception:
        checks["postgres"] = "down"

    # Redis
    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "down"

    return success({"checks": checks})
