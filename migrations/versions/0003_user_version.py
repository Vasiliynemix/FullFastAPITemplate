"""add version_id to users (optimistic locking)

Revision ID: 0003_user_version
Revises: 0002_outbox
Create Date: 2026-06-08 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_user_version"
down_revision: str | None = "0002_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default="1" — чтобы существующие строки получили версию. Дальше значением
    # управляет ORM (version_id_col): задаёт на INSERT, инкрементит на UPDATE.
    op.add_column(
        "users",
        sa.Column("version_id", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("users", "version_id")
