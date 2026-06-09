"""
Конфигурация приложения на Pydantic Settings.

Все настройки читаются из окружения / .env. Settings кэшируется через lru_cache,
чтобы не пересобирать объект на каждый запрос (важно для hot paths).
"""

from __future__ import annotations

import multiprocessing
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Абсолютный путь к .env (корень проекта = parents[2] от config.py: app/core/config.py).
# Берём по абсолютному пути, чтобы .env находился независимо от рабочего каталога —
# например при запуске/дебаге из PyCharm, где CWD может быть не корнем проекта.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Environment(StrEnum):
    DEV = "dev"
    PROD = "prod"


class BrokerType(StrEnum):
    MEMORY = "memory"
    KAFKA = "kafka"
    RABBITMQ = "rabbitmq"


class AcquirerName(StrEnum):
    """Имена платёжных систем — ключи реестра эквайринга (а не строки)."""

    MEMORY = "memory"  # заглушка (в проде запрещена)
    YOOKASSA = "yookassa"
    # ── новый провайдер: <NAME> = "<name>"


class AuthTransport(StrEnum):
    """Где живёт токен в авторизации — что-то ОДНО (не оба сразу)."""

    HEADER = "header"  # Authorization: Bearer (SPA/mobile; иммунен к CSRF)
    COOKIE = "cookie"  # HttpOnly-кука (браузерная сессия; иммунна к XSS-краже токена)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App ---
    app_name: str = "fastapi"
    environment: Environment = Environment.DEV
    debug: bool = True
    host: str = "0.0.0.0"
    port: int = 8080
    api_v1_prefix: str = "/api/v1"
    cors_origins: str = "http://localhost:3000"

    # --- PostgreSQL ---
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "app"
    postgres_password: str = "app"
    postgres_db: str = "app"
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_timeout: int = 5
    db_pool_recycle: int = 1800
    db_echo: bool = False
    # Подключаемся через PgBouncer (transaction pooling)? Тогда отключаем prepared
    # statements asyncpg (несовместимы с transaction-режимом). См. app/db/session.py.
    db_pgbouncer: bool = False

    # --- Redis ---
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""
    redis_max_connections: int = 100
    redis_default_ttl: int = 60

    # --- Rate limit ---
    rate_limit_enabled: bool = True
    # Длинное окно: сколько запросов с одного IP за rate_limit_window секунд.
    rate_limit_requests: int = 1000
    rate_limit_window: int = 60
    # Короткое (burst) окно против спам-всплесков: не больше rate_limit_burst
    # запросов за rate_limit_burst_window секунд. 0 = ярус выключен.
    # Пример: 20 / 1с не даст «выстрелить» всю минутную квоту за секунду.
    rate_limit_burst: int = 0
    rate_limit_burst_window: int = 1

    # --- Search (умный поиск q, pg_trgm) ---
    # Порог триграммной похожести для q. Ниже => терпимее к опечаткам (ловит даже
    # перестановки букв, напр. «всая»~«вася»≈0.11), но больше слабых совпадений.
    # Выше (напр. 0.3) => строже/точнее. На SQLite не используется (там ILIKE-фолбэк).
    search_similarity_threshold: float = 0.1

    # --- Broker ---
    # broker_enabled=false => не поднимаем брокер и консьюмеров, не публикуем события,
    # ручки /notifications и outbox-relay отключаются (если события не нужны).
    broker_enabled: bool = True
    broker_type: BrokerType = BrokerType.MEMORY
    broker_url: str = ""
    broker_default_topic: str = "events"

    # --- Outbox (transactional outbox) ---
    # Релей крутится в воркере: раз в interval секунд публикует неотправленные события.
    outbox_enabled: bool = True
    outbox_relay_interval: float = 5.0  # период опроса outbox релеем, сек
    outbox_batch_size: int = 100  # сколько строк публиковать за один проход
    outbox_retention_days: int = 7  # через сколько дней чистить опубликованные строки

    # --- Object storage (S3-совместимое) ---
    # storage_enabled=false (по умолчанию) => не подключаем S3 и не регистрируем /files.
    # Включайте, только если реально нужна работа с файлами.
    storage_enabled: bool = False
    storage_presign_expire: int = 3600  # срок жизни presigned-ссылки, сек
    # endpoint_url пустой => AWS S3; задайте для MinIO/Yandex Object Storage.
    s3_endpoint_url: str = ""
    s3_region: str = "us-east-1"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_bucket: str = ""
    s3_use_ssl: bool = True
    # Публичный базовый URL (публичный бакет/CDN). Если задан — presigned_url отдаёт
    # прямую ссылку без подписи; иначе генерируется подписанная временная ссылка.
    s3_public_url: str = ""

    # --- Внешний API сообщений (MessagesClient) ---
    messages_api_base_url: str = ""
    messages_api_key: str = ""

    # --- Acquiring (платёжный эквайринг) ---
    # Платёжных систем может быть НЕСКОЛЬКО одновременно — у каждой свой *_enabled флаг.
    # Фабрика собирает все включённые (app/acquiring/factory.py). Добавить провайдера =
    # реализация + флаг тут + одна ветка в фабрике.
    acquiring_currency: str = "RUB"  # валюта по умолчанию (ISO 4217)
    acquiring_return_url: str = ""  # куда вернуть пользователя после оплаты
    # memory — заглушка без сети (тесты/локалка). В ПРОДЕ запрещена (валидатор ниже).
    acquiring_memory_enabled: bool = False
    # YooKassa: креды shopId/секретный ключ из ЛК (SDK ходит по HTTP Basic).
    yookassa_enabled: bool = False
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    # ── новый провайдер: <name>_enabled: bool = False + его креды

    # --- Logging ---
    log_level: str = "INFO"
    # Формат КОНСОЛИ: false = человекочитаемо (rich-tracebacks), true = JSON.
    log_json: bool = False
    # Дополнительно писать JSON-логи в ФАЙЛ (обычно только в проде). Файл всегда JSON.
    # Ротация по размеру + gzip-сжатие архивов + ограничение их числа (см. ниже).
    log_file_enabled: bool = False
    log_file_path: str = "logs/app.log"
    log_file_max_bytes: int = 52_428_800  # 50 MB на файл до ротации
    log_file_backup_count: int = 14  # сколько архивов хранить (старые удаляются)

    # --- Gunicorn ---
    web_concurrency: int = 0
    gunicorn_timeout: int = 30
    gunicorn_graceful_timeout: int = 30
    gunicorn_keepalive: int = 5

    # --- Auth / JWT ---
    # Главный переключатель JWT-авторизации. Если false — JWT-зависимости на ручках
    # не требуют токен и не проверяют роли (режим «только глобальный ключ»).
    # ОГРАНИЧЕНИЕ: при auth_jwt_enabled=false обязателен global_api_key_enabled=true
    # (см. валидатор) — нельзя оставить сервис вообще без защиты.
    auth_jwt_enabled: bool = True
    # ВАЖНО: в проде задайте длинный случайный секрет (например `openssl rand -hex 32`).
    # Дефолт ниже >=32 байт (требование HS256), но он НЕ секретен — только для dev.
    jwt_secret_key: str = "dev-insecure-secret-change-me-please-32b+"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15  # короткоживущий access
    refresh_token_expire_days: int = 7  # долгоживущий refresh (с ротацией)
    # Если true — каждый авторизованный запрос сверяет sid с SessionStore (1 GET в Redis).
    # Даёт НЕМЕДЛЕННУЮ инвалидацию access после logout/удаления (ценой обращения к Redis).
    # Если false (по умолчанию) — access stateless, отзыв действует после его истечения.
    auth_validate_session: bool = False
    # Транспорт токена в авторизации — ОДНО из: header (Bearer) ИЛИ cookie (HttpOnly).
    # Работает только при включённом JWT; cookie несовместим с глобальным ключом
    # (браузеру негде хранить секрет) — см. валидатор ниже.
    auth_token_transport: AuthTransport = AuthTransport.HEADER
    auth_cookie_secure: bool = True  # Secure-флаг (только HTTPS). В локальном http-dev = false.
    auth_cookie_samesite: str = "lax"  # lax | strict | none (none требует secure=true)

    # --- Global API key gate ---
    # Если включено — ВЕСЬ API (кроме health/docs) требует заголовок X-API-Key.
    # Это «сервис закрыт одним ключом» — для доступа только доверенных продуктов.
    # Работает независимо от JWT: можно включить только gate, только JWT, или оба.
    global_api_key_enabled: bool = False
    global_api_key: str = ""

    # --- Sentry (error tracking) ---
    # Пусто => Sentry выключен (no-op). Задайте DSN из проекта Sentry, чтобы включить.
    sentry_dsn: str = ""
    # Доля транзакций для performance-трейсинга (0.0 = только ошибки, без APM).
    sentry_traces_sample_rate: float = 0.0
    # Версия релиза для группировки ошибок (например git sha). Пусто = не задавать.
    sentry_release: str = ""

    # --- Docs (Swagger / ReDoc / openapi.json) ---
    # Мастер-переключатель документации. Безопасный дефолт: в ПРОДЕ docs показываются
    # ТОЛЬКО если заданы логин+пароль (basic auth), иначе скрыты (см. _docs_visible).
    docs_enabled: bool = True
    # Если оба поля заданы — /docs, /redoc, /openapi.json закрываются HTTP Basic Auth.
    docs_basic_auth_user: str = ""
    docs_basic_auth_password: str = ""

    # --- Health ---
    # Показывать активный конфиг в /health/ready. /ready открыт (без авторизации), а конфиг
    # раскрывает стек/защитную конфигурацию — поэтому в проде по умолчанию СКРЫТ. В dev
    # показывается всегда (для удобства). true => показывать и в проде (на свой риск).
    health_expose_config: bool = False

    @model_validator(mode="after")
    def _validate_auth_modes(self) -> Settings:
        # Нельзя выключить ОБА контура — сервис остался бы без защиты
        if not self.auth_jwt_enabled and not self.global_api_key_enabled:
            raise ValueError(
                "AUTH_JWT_ENABLED=false требует GLOBAL_API_KEY_ENABLED=true "
                "(нельзя оставить сервис без авторизации совсем)"
            )
        # Cookie-транспорт осмыслен только при JWT и несовместим с глобальным ключом
        # (браузеру негде безопасно хранить секретный ключ). Падаем явно, а не молча.
        if self.auth_token_transport is AuthTransport.COOKIE:
            if not self.auth_jwt_enabled:
                raise ValueError("AUTH_TOKEN_TRANSPORT=cookie требует AUTH_JWT_ENABLED=true")
            if self.global_api_key_enabled:
                raise ValueError(
                    "AUTH_TOKEN_TRANSPORT=cookie несовместим с GLOBAL_API_KEY_ENABLED=true: "
                    "браузеру негде хранить секретный ключ. Используйте header-транспорт "
                    "или выключите глобальный ключ."
                )
        return self

    @model_validator(mode="after")
    def _validate_acquiring(self) -> Settings:
        # memory-эквайринг — заглушка без реальных платежей; в проде это недопустимо.
        # Падаем явно при старте, а не «принимаем игрушечные платежи» в продакшене.
        if self.is_prod and self.acquiring_memory_enabled:
            raise ValueError(
                "ACQUIRING_MEMORY_ENABLED=true запрещён в проде (memory — заглушка). "
                "Выключите его и включите реальный провайдер (например YOOKASSA_ENABLED)."
            )
        # Включённый провайдер без кред — мисконфигурация: падаем на старте, а не на
        # первом платеже. Новый провайдер -> добавьте сюда проверку его обязательных полей.
        if self.yookassa_enabled and not (self.yookassa_shop_id and self.yookassa_secret_key):
            raise ValueError("YOOKASSA_ENABLED=true требует YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY")
        return self

    @property
    def cookie_auth(self) -> bool:
        """Активен ли cookie-транспорт токена (а не header)."""
        return self.auth_token_transport is AuthTransport.COOKIE

    # ------------------------------------------------------------------
    # Производные значения
    # ------------------------------------------------------------------
    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_prod(self) -> bool:
        return self.environment is Environment.PROD

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        # asyncpg-драйвер для async-движка SQLAlchemy
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def redis_url(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def workers(self) -> int:
        # Явное значение приоритетно (WEB_CONCURRENCY). Если не задано — формула.
        # Для АСИНХРОННЫХ воркеров (UvicornWorker) берём CPU+1, а не классические
        # (2*CPU)+1 (та формула для СИНХРОННЫХ воркеров): один async-воркер на своём
        # event loop держит много одновременных IO-bound запросов, поэтому много
        # процессов на ядро не нужно.
        # ВНИМАНИЕ: при нескольких репликах задавайте WEB_CONCURRENCY ЯВНО —
        # cpu_count() возвращает ядра ХОСТА, игнорируя cgroup-лимит контейнера.
        if self.web_concurrency > 0:
            return self.web_concurrency
        return multiprocessing.cpu_count() + 1


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton-конфиг. Кэшируется на весь процесс."""
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
