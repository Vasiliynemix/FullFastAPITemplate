"""
DDL-хелперы для МИГРАЦИЙ: расширение pg_trgm и GIN-триграммные индексы умного поиска.

Чтобы не писать сырой SQL в каждой миграции. Использование в миграции Alembic:

    from app.db.ddl import ensure_pg_trgm, create_trgm_index, drop_trgm_index

    def upgrade() -> None:
        ensure_pg_trgm()
        create_trgm_index("ix_users_full_name_trgm", "users", "full_name")

    def downgrade() -> None:
        drop_trgm_index("ix_users_full_name_trgm", "users")

Все функции — no-op вне Postgres (если кто-то прогонит миграции на SQLite). alembic.op
импортируется лениво — модуль безопасно импортировать и вне контекста миграции.
"""

from __future__ import annotations


def _is_postgres() -> bool:
    from alembic import op

    return op.get_bind().dialect.name == "postgresql"


def ensure_pg_trgm() -> None:
    """Создать расширение pg_trgm (идемпотентно). Нужно один раз на БД."""
    from alembic import op

    if _is_postgres():
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def create_trgm_index(name: str, table: str, *columns: str) -> None:
    """GIN-индекс триграмм через op.create_index (без сырого SQL)."""
    from alembic import op

    if not _is_postgres():
        return
    op.create_index(
        name,
        table,
        list(columns),
        postgresql_using="gin",
        postgresql_ops=dict.fromkeys(columns, "gin_trgm_ops"),
        if_not_exists=True,
    )


def drop_trgm_index(name: str, table: str) -> None:
    from alembic import op

    if _is_postgres():
        op.drop_index(name, table_name=table, if_exists=True)
