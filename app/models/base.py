"""
Базовый класс ORM (SQLAlchemy 2.x, декларативный, типизированный).

Содержит общие колонки id/created_at/updated_at и единое соглашение об именах
индексов/ограничений — это упрощает миграции Alembic.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DDL, Index, Integer, MetaData, event, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

# Соглашение об именах — детерминированные имена constraint'ов для Alembic
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# Авто-создание расширения pg_trgm ПЕРЕД созданием таблиц (create_all). Только Postgres
# (на SQLite — пропускается). Так умный поиск работает без ручного `CREATE EXTENSION`.
# В прод-миграциях расширение создаётся хелпером ensure_pg_trgm (см. app/db/ddl.py).
event.listen(
    Base.metadata,
    "before_create",
    DDL("CREATE EXTENSION IF NOT EXISTS pg_trgm").execute_if(dialect="postgresql"),
)


def trgm_index(name: str, *columns: str) -> Index:
    """
    Декларативный GIN-индекс триграмм (pg_trgm) для умного поиска — кладётся в
    __table_args__ модели. БЕЗ сырого SQL: SQLAlchemy сам рендерит
    `USING gin (col gin_trgm_ops)` на Postgres. На других диалектах (SQLite-тесты)
    dialect-опции игнорируются → создаётся обычный индекс (безвредно).

        class User(...):
            __table_args__ = (trgm_index("ix_users_full_name_trgm", "full_name"),)
    """
    return Index(
        name,
        *columns,
        postgresql_using="gin",
        postgresql_ops=dict.fromkeys(columns, "gin_trgm_ops"),
    )


class TimestampCreatedMixin:
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now(), nullable=False)


class TimestampMixin:
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )


class VersionedMixin:
    """
    Оптимистичная блокировка (защита от lost update без удержания локов).

    Колонка version_id, помеченная как version_id_col, заставляет ORM:
    * на INSERT — задать начальную версию;
    * на каждый UPDATE — добавить `WHERE version_id = <прочитанная>` и инкрементить её.

    Если за время между чтением и записью строку изменил кто-то ещё — UPDATE затронет
    0 строк, и SQLAlchemy бросит StaleDataError (сервис превращает её в 409 Conflict).

    Работает ТОЛЬКО для ORM-апдейтов (load -> mutate -> commit). Bulk-апдейты Core
    (update_by_id) версию НЕ проверяют — это by design.
    """

    version_id: Mapped[int] = mapped_column(Integer, nullable=False)

    @declared_attr.directive
    def __mapper_args__(cls) -> dict:  # noqa: N805
        return {"version_id_col": cls.version_id}
