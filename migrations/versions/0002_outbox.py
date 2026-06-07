"""outbox_messages table (transactional outbox)

Revision ID: 0002_outbox
Revises: 0001_initial
Create Date: 2026-06-07 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_outbox"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox_messages",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("topic", sa.String(length=255), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_outbox_messages"),
    )
    # Партиальный индекс под relay-запрос: только неопубликованные, по порядку создания.
    # Индекс остаётся компактным (по мере публикации строки из него «выпадают»).
    op.create_index(
        "ix_outbox_unpublished",
        "outbox_messages",
        ["created_at"],
        postgresql_where=sa.text("published_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_unpublished", table_name="outbox_messages")
    op.drop_table("outbox_messages")
