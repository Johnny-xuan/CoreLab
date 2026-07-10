"""phase 8 agent_policy table

Revision ID: 4a8d2c9f1e7b
Revises: 3f1a8e92cd64
Create Date: 2026-06-05 04:00:00.000000+00:00

Adds the ``agent_policy`` table per docs/02 §5.18 — per-server soft
tuning (8 policy_key x 4 severity routing) that pairs with
``agent_capability`` hard gates.

profile preset (permissive / standard / strict) lives in the
``agent_policy_service`` and seeds 8 rows per server through a single
transaction on server enrollment.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "4a8d2c9f1e7b"  # pragma: allowlist secret
down_revision: str | None = "3f1a8e92cd64"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_policy",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False, autoincrement=True),
        sa.Column("server_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("policy_key", mysql.VARCHAR(64), nullable=False),
        sa.Column(
            "enabled",
            mysql.TINYINT(1),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("severity", mysql.VARCHAR(16), nullable=False),
        sa.Column("threshold_value", sa.Integer(), nullable=True),
        sa.Column("grace_period_seconds", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column(
            "notify_admin",
            mysql.TINYINT(1),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("notes", mysql.VARCHAR(500), nullable=True),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            server_onupdate=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.Column("updated_by_user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("server_id", "policy_key", name="uq_policy_server_key"),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["server.id"],
            name="fk_policy_server",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["user.id"],
            name="fk_policy_updater",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.CheckConstraint(
            "severity IN ('log_only','notify','warn','auto_kill')",
            name="ck_policy_severity",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )


def downgrade() -> None:
    op.drop_table("agent_policy")
