"""
Кастомизация OpenAPI-схемы (Swagger / ReDoc).

Главное: gate по `X-API-Key` реализован ASGI-middleware, а не FastAPI-зависимостью,
поэтому сам по себе он НЕ попадает в OpenAPI. Здесь, когда GLOBAL_API_KEY_ENABLED=true,
мы вручную добавляем security-схему `ApiKeyAuth` — тогда в Swagger появляется поле
«Authorize» для ключа, и UI начинает слать заголовок X-API-Key на все запросы.

Bearer-схема (JWT) добавляется FastAPI автоматически на ручках с HTTPBearer-зависимостью.
"""

from __future__ import annotations

import secrets

from fastapi import Depends, FastAPI, HTTPException
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.core.config import settings

# Статическая «шапка» описания (одинакова во всех режимах).
_DESCRIPTION_HEAD = """
Высоконагруженный API-сервис (шаблон).

## Единый контракт ответов
Любой ответ имеет вид:
```json
{ "status": true,  "data": { ... }, "meta": { "request_id": "..." } }   // успех
{ "status": false, "data": { "code": "not_found", "message": "..." }, "meta": {...} }  // ошибка
```
`request_id` есть в каждом ответе и в заголовке `X-Request-ID` — для сквозной трассировки.
"""

# Статический «хвост» (одинаков во всех режимах).
_DESCRIPTION_TAIL = """
## Прочее
* Пагинация: `?page=1&per_page=50`, метаданные в `meta` (`total`, `pages`).
* Стриминг больших коллекций: `GET /users/stream/all` (формат NDJSON).
* Идемпотентность POST: заголовок `Idempotency-Key`.
* Rate limit: заголовки `X-RateLimit-*`, при превышении — `429`.
"""

# Описания тегов. Тег auth добавляется только в JWT-режиме (см. build_tags).
_TAG_HEALTH = {"name": "health", "description": "Liveness/readiness пробы. Без авторизации."}
_TAG_AUTH = {"name": "auth", "description": "Регистрация, вход, JWT-токены, сессии."}
_TAG_USERS = {"name": "users", "description": "CRUD пользователей, пагинация, NDJSON-стриминг."}
_TAG_ACCOUNTS = {
    "name": "accounts",
    "description": "Демо связей (1-1/1-many/many-1/many-many) + eager-load + FOR UPDATE.",
}
_TAG_NOTIFICATIONS = {
    "name": "notifications",
    "description": "Постановка уведомлений в очередь (брокер).",
}
_TAG_FILES = {
    "name": "files",
    "description": "Загрузка/скачивание файлов в S3-совместимом объектном хранилище.",
}


def _auth_description() -> str:
    """Секция «Авторизация» — только про реально включённые способы."""
    lines = ["## Авторизация"]
    if settings.auth_jwt_enabled:
        lines.append(
            "* **JWT** — `POST /auth/login` → `access_token` + `refresh_token`. "
            "Защищённые ручки требуют `Authorization: Bearer <access_token>`. "
            "Роли: user/manager/admin/service."
        )
    if settings.global_api_key_enabled:
        lines.append(
            "* **Global API key** — заголовок `X-API-Key` обязателен на всём API "
            "(кроме health/docs). Нажмите **Authorize** и введите ключ."
        )
    if settings.auth_jwt_enabled and settings.global_api_key_enabled:
        lines.append("\nЗащищённые ручки требуют **оба** заголовка вместе: `X-API-Key` и `Bearer`.")
    elif not settings.auth_jwt_enabled:
        lines.append(
            "\nРежим **service-to-service**: доступ только по `X-API-Key`; "
            "пользовательские ручки `/auth/*` отключены."
        )
    return "\n".join(lines)


def build_description() -> str:
    """Полное описание для Swagger/ReDoc с учётом активного режима авторизации."""
    return f"{_DESCRIPTION_HEAD}\n{_auth_description()}\n{_DESCRIPTION_TAIL}"


def build_tags() -> list[dict[str, str]]:
    """Метаданные тегов; auth — только когда включён JWT (иначе ручек нет)."""
    tags = [_TAG_HEALTH]
    if settings.auth_jwt_enabled:
        tags.append(_TAG_AUTH)
    tags.append(_TAG_USERS)
    tags.append(_TAG_ACCOUNTS)
    if settings.broker_enabled:
        tags.append(_TAG_NOTIFICATIONS)
    if settings.storage_enabled:
        tags.append(_TAG_FILES)
    return tags


def _strip_jwt(schema: dict) -> None:
    """
    Убирает Bearer-схему из OpenAPI, когда JWT выключен (режим «только глобал»):
    схема не должна предлагать токен, которого сервис не требует.
    """
    schema.get("components", {}).get("securitySchemes", {}).pop("HTTPBearer", None)
    for operations in schema.get("paths", {}).values():
        for operation in operations.values():
            if not isinstance(operation, dict):
                continue
            sec = operation.get("security")
            if not sec:
                continue
            cleaned = [{k: v for k, v in req.items() if k != "HTTPBearer"} for req in sec]
            cleaned = [req for req in cleaned if req]  # выкидываем опустевшие требования
            if cleaned:
                operation["security"] = cleaned
            else:
                operation.pop("security", None)


def _apply_api_key_gate(schema: dict) -> None:
    """
    Добавляет схему ApiKeyAuth и требует X-API-Key на КАЖДОЙ операции (кроме health).

    Ключевой момент семантики OpenAPI: чтобы Swagger слал X-API-Key ВМЕСТЕ с Bearer,
    оба должны быть в ОДНОМ объекте требования (логическое И). Поэтому подмешиваем
    ApiKeyAuth в существующее требование операции, а не ставим глобально (глобальное
    переопределяется per-operation security от HTTPBearer, и ключ «отваливается»).
    """
    schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
    schemes["ApiKeyAuth"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": (
            "Глобальный ключ доступа к сервису (GLOBAL_API_KEY). "
            "Требуется на всех ручках, кроме /health и /docs."
        ),
    }

    # Health исключён из gate (как и в middleware) — туда ключ не добавляем
    exempt_prefix = f"{settings.api_v1_prefix}/health"

    for path, operations in schema.get("paths", {}).items():
        if path.startswith(exempt_prefix):
            continue
        for method, operation in operations.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            existing = operation.get("security")
            if existing:
                # Подмешиваем ключ в каждое требование -> «Bearer И ApiKey»
                operation["security"] = [{**req, "ApiKeyAuth": []} for req in existing]
            else:
                # Публичная (для JWT) ручка, но gate всё равно требует ключ
                operation["security"] = [{"ApiKeyAuth": []}]


def configure_openapi(app: FastAPI) -> None:
    """Подменяет app.openapi на версию с доп. security-схемой X-API-Key."""

    def _custom_openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
        )

        # Порядок важен: сначала убираем Bearer (если JWT off), затем добавляем ключ —
        # тогда операции, оставшиеся без security, получат требование X-API-Key.
        if not settings.auth_jwt_enabled:
            _strip_jwt(schema)
        if settings.global_api_key_enabled:
            _apply_api_key_gate(schema)

        app.openapi_schema = schema
        return schema

    app.openapi = _custom_openapi  # type: ignore[method-assign]


# ----------------------------------------------------------------------
# Документация (Swagger/ReDoc) с опциональным Basic Auth
# ----------------------------------------------------------------------
def _docs_has_basic_auth() -> bool:
    return bool(settings.docs_basic_auth_user and settings.docs_basic_auth_password)


def _docs_visible() -> bool:
    """Показывать ли docs. Безопасный дефолт: в проде — только при заданном Basic Auth."""
    if not settings.docs_enabled:
        return False
    # В проде без Basic Auth документацию не публикуем (защита по умолчанию)
    return not (settings.is_prod and not _docs_has_basic_auth())


def _docs_auth_dependency():
    """Зависимость Basic Auth для docs, если заданы логин+пароль. Иначе None (docs открыты)."""
    user = settings.docs_basic_auth_user
    password = settings.docs_basic_auth_password
    if not (user and password):
        return None

    security = HTTPBasic(auto_error=True)

    async def _verify(credentials: HTTPBasicCredentials = Depends(security)) -> None:
        # Сравнение в постоянном времени (защита от timing-атак)
        user_ok = secrets.compare_digest(credentials.username, user)
        pass_ok = secrets.compare_digest(credentials.password, password)
        if not (user_ok and pass_ok):
            raise HTTPException(
                status_code=401,
                detail="Invalid documentation credentials",
                headers={"WWW-Authenticate": "Basic"},
            )

    return _verify


def register_docs(app: FastAPI) -> None:
    """
    Регистрирует /docs, /redoc, /openapi.json вручную (FastAPI-автодоки отключены).
    Если заданы DOCS_BASIC_AUTH_* — ручки закрыты HTTP Basic Auth. В проде без логина/
    пароля docs не публикуются вовсе (_docs_visible).
    """
    if not _docs_visible():
        return

    verify = _docs_auth_dependency()
    deps = [Depends(verify)] if verify else []

    @app.get("/openapi.json", include_in_schema=False, dependencies=deps)
    async def _openapi_json() -> JSONResponse:
        return JSONResponse(app.openapi())

    @app.get("/docs", include_in_schema=False, dependencies=deps)
    async def _swagger() -> HTMLResponse:
        return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} — Swagger")

    @app.get("/redoc", include_in_schema=False, dependencies=deps)
    async def _redoc() -> HTMLResponse:
        return get_redoc_html(openapi_url="/openapi.json", title=f"{app.title} — ReDoc")
