"""
Async-движок и фабрика сессий SQLAlchemy 2.x.

Тюнинг пула под высокую нагрузку:
* pool_size / max_overflow — берутся из настроек; формула планирования:
      (pool_size + max_overflow) * workers <= postgres.max_connections
* pool_pre_ping=True — отбраковывает «протухшие» соединения без падения запроса.
* pool_recycle — пересоздание соединений раньше серверного таймаута.
* pool_timeout — быстрый отказ вместо зависания под нагрузкой.
* expire_on_commit=False — объекты остаются пригодны после commit (меньше лишних SELECT).
* asyncpg + server_settings jit=off — стабильная латентность на коротких запросах.

Движок создаётся ЛЕНИВО (один на процесс-воркер) и переиспользуется.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def create_engine() -> AsyncEngine:
    connect_args: dict[str, object] = {"command_timeout": 10}

    if settings.db_pgbouncer:
        # PgBouncer (transaction pooling) НЕ совместим с prepared statements asyncpg:
        # соединение к PG меняется между запросами. Отключаем оба кэша prepared statements.
        # server_settings (jit/application_name) тоже не шлём — pgbouncer может отвергнуть
        # неизвестные startup-параметры (на его стороне см. IGNORE_STARTUP_PARAMETERS).
        #
        # ВАЖНО (следствие отключения prepared statements): «голый» НЕтипизированный
        # параметр в СЫРОМ SQL ломается, т.к. PostgreSQL не может вывести его тип:
        #     text("SELECT :n")            -> ошибка "expected str, got int"
        #     text("SELECT CAST(:n AS INT)")  или  text("SELECT :n::int")  -> OK
        # ORM-запросы (по типизированным колонкам) и параметры без типовой
        # неоднозначности работают штатно — это касается только сырого SQL.
        connect_args["statement_cache_size"] = 0  # кэш asyncpg
        connect_args["prepared_statement_cache_size"] = 0  # кэш SQLAlchemy-asyncpg
    else:
        # Прямое подключение к PostgreSQL — используем серверные настройки
        connect_args["server_settings"] = {
            "jit": "off",  # JIT PG вредит коротким частым запросам
            "application_name": settings.app_name,
        }

    return create_async_engine(
        settings.database_url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_engine()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


async def dispose_engine() -> None:
    """Закрыть пул на shutdown (graceful)."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """
    FastAPI-зависимость: выдаёт сессию на запрос.
    Транзакцией управляет Unit of Work / сервисный слой, не сама зависимость.
    """
    async with get_sessionmaker()() as session:
        yield session
