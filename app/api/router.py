"""Сборка корневого роутера API v1.

Роутер собирается ФУНКЦИЕЙ (в create_app), а не на импорте — чтобы состав ручек
зависел от текущих настроек/флагов и был предсказуем в тестах.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import accounts, auth, categories, files, health, notifications, users
from app.core.config import settings


def build_api_router() -> APIRouter:
    router = APIRouter()
    router.include_router(health.router, tags=["health"])

    # /auth (login/refresh/logout/me/sessions) — только при включённом JWT.
    # В режиме «только глобальный ключ» (AUTH_JWT_ENABLED=false) сервис чисто
    # service-to-service, пользовательской авторизации нет.
    if settings.auth_jwt_enabled:
        router.include_router(auth.router, prefix="/auth", tags=["auth"])

    router.include_router(users.router, prefix="/users", tags=["users"])
    router.include_router(accounts.router, prefix="/accounts", tags=["accounts"])
    router.include_router(categories.router, prefix="/categories", tags=["accounts"])

    # /notifications работает через брокер — без него ручку не подключаем.
    if settings.broker_enabled:
        router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])

    # /files работает через объектное хранилище — подключаем только если оно включено.
    if settings.storage_enabled:
        router.include_router(files.router, prefix="/files", tags=["files"])

    return router
