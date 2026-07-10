"""phase 7 notification table

Revision ID: 3f1a8e92cd64
Revises: 0e7c5d3a2f9b
Create Date: 2026-06-05 03:00:00.000000+00:00

Adds the ``notification`` table per docs/02 §5.14 — bell + WS push
storage that backs Phase 7 lifecycle alerts (reservation.started /
.completed / .failed / .cancelled_by_other + link.prepared).

FK ON DELETE CASCADE because notifications are owned by their
recipient; user soft-disable wipes the rows.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "3f1a8e92cd64"  # pragma: allowlist secret
down_revision: str | None = "0e7c5d3a2f9b"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False, autoincrement=True),
        sa.Column("recipient_user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("type", mysql.VARCHAR(64), nullable=False),
        sa.Column(
            "severity",
            mysql.VARCHAR(16),
            nullable=False,
            server_default="info",
        ),
        sa.Column("title", mysql.VARCHAR(255), nullable=False),
        sa.Column("body", mysql.TEXT, nullable=True),
        sa.Column("payload", mysql.JSON, nullable=True),
        sa.Column("cta_url", mysql.VARCHAR(500), nullable=True),
        sa.Column(
            "is_read",
            mysql.TINYINT(1),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("read_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"],
            ["user.id"],
            name="fk_notif_recipient",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.CheckConstraint(
            "severity IN ('info','warn','error')",
            name="ck_notif_severity",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index(
        "idx_notif_recipient_unread",
        "notification",
        ["recipient_user_id", "is_read", "created_at"],
    )
    op.create_index("idx_notif_created", "notification", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_notif_created", table_name="notification")
    op.drop_index("idx_notif_recipient_unread", table_name="notification")
    op.drop_table("notification")
