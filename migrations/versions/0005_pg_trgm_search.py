"""pg_trgm + GIN-индексы для умного поиска (users.full_name/email)

Revision ID: 0005_pg_trgm
Revises: 0004_accounts
Create Date: 2026-06-08 00:00:00.000000

Без сырого SQL: расширение и GIN-индексы создаются хелперами app/db/ddl.py.
Индексы также объявлены декларативно в модели (trgm_index в User.__table_args__) —
это источник правды для create_all (dev/тесты) и автогенерации миграций.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.db.ddl import create_trgm_index, drop_trgm_index, ensure_pg_trgm

revision: str = "0005_pg_trgm"
down_revision: str | None = "0004_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    ensure_pg_trgm()
    create_trgm_index("ix_users_full_name_trgm", "users", "full_name")
    create_trgm_index("ix_users_email_trgm", "users", "email")


def downgrade() -> None:
    drop_trgm_index("ix_users_email_trgm", "users")
    drop_trgm_index("ix_users_full_name_trgm", "users")
