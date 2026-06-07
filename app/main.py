"""
Точка сборки FastAPI-приложения (application factory).

Решения под нагрузку:
* Сериализация ответов — через response_model (Pydantic v2). Под капотом
  pydantic-core (Rust) кодирует модель в JSON без промежуточного jsonable_encoder,
  что даёт лучшую производительность; кастомный класс ответа не нужен.
* Минимальный и упорядоченный стек middleware (чистый ASGI, без BaseHTTPMiddleware).
  Порядок (снаружи внутрь): SecurityHeaders -> RequestContext -> ApiKey -> RateLimit -> CORS.
* docs отключаются в prod (меньше поверхность атаки, ничего лишнего в hot path).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import build_api_router
from app.core.config import settings
from app.core.lifespan import lifespan
from app.core.observability import init_sentry
from app.core.openapi import build_description, build_tags, configure_openapi, register_docs
from app.exceptions.handlers import register_exception_handlers
from app.middleware import (
    ApiKeyMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)


def create_app() -> FastAPI:
    # Sentry инициализируем как можно раньше (no-op без SENTRY_DSN), чтобы его
    # ASGI/Starlette-инструментация покрыла приложение.
    init_sentry()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
        description=build_description(),
        openapi_tags=build_tags(),
        # Автодоки FastAPI отключаем — регистрируем свои (с опциональным Basic Auth)
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    # CORS — добавляется первым, отрабатывает последним (ближе всего к приложению)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        max_age=600,  # кэш preflight, меньше OPTIONS-запросов
    )
    app.add_middleware(RateLimitMiddleware)
    # ApiKey-gate должен видеть request_id (для логов) -> добавляется ПОСЛЕ RateLimit,
    # но ДО RequestContext, чтобы в стеке исполнения оказаться между ними:
    #   Security -> RequestContext -> ApiKey -> RateLimit -> CORS -> app
    app.add_middleware(ApiKeyMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    register_exception_handlers(app)
    app.include_router(build_api_router(), prefix=settings.api_v1_prefix)

    # Кастомный OpenAPI: добавляет схему X-API-Key, когда включён глобальный gate
    configure_openapi(app)
    # Свои /docs, /redoc, /openapi.json (опционально под Basic Auth; в проде — только с ним)
    register_docs(app)

    return app


app = create_app()

if __name__ == "__main__":
    # Запуск в ЭТОМ ЖЕ процессе без reload — чтобы срабатывали брейкпоинты в дебаге
    # (PyCharm/VS Code). dev() с --reload поднимает uvicorn подпроцессом, к которому
    # дебаггер не подключается. Для обычного дев-запуска с автоперезагрузкой — `make dev`.
    import uvicorn

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )
