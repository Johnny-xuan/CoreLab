"""phase 5 reservation table

Revision ID: ff9621ab430d
Revises: 68e16808e68d
Create Date: 2026-06-04 13:00:22.093520+00:00

Creates the single Phase 5 table ``reservation`` matching
docs/02-data-model.md §5.13. v0.2 reframing already baked in:
``account_link_id`` NOT NULL (per-PA UI injects from route context;
service layer rejects ``source='admin_declared'``), ``gpu_memory_mb``
NULL = exclusive / non-null = shared (sum ≤ gpu.memory_total_mb),
``script_scheduled_start_at`` allows ``>= start_at && < end_at``.

7 indexes per doc §5.13 DDL line 1053-1059. 4 CHECK constraints per
doc DDL line 1071-1080. FK ``account_link_id`` is RESTRICT so revoking
a link does not orphan a reservation (per doc line 1066-1068).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "ff9621ab430d"  # pragma: allowlist secret
down_revision: str | None = "68e16808e68d"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reservation",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("server_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("gpu_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("account_link_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("group_id", mysql.CHAR(36), nullable=True),
        sa.Column("start_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("end_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column(
            "status",
            mysql.VARCHAR(32),
            server_default=sa.text("'scheduled'"),
            nullable=False,
        ),
        sa.Column("gpu_memory_mb", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("gpu_compute_share_pct", mysql.TINYINT(unsigned=True), nullable=True),
        sa.Column("script", mysql.TEXT(), nullable=True),
        sa.Column("script_scheduled_start_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("script_max_runtime_seconds", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("script_started_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("script_finished_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("script_exit_code", mysql.INTEGER(), nullable=True),
        sa.Column("script_output_size_bytes", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("script_log_path", mysql.VARCHAR(255), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column("cancelled_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("cancelled_by_user_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("cancellation_reason", mysql.VARCHAR(255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name="fk_res_user",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["server.id"],
            name="fk_res_server",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["gpu_id"],
            ["gpu.id"],
            name="fk_res_gpu",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["account_link_id"],
            ["account_link.id"],
            name="fk_res_link",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by_user_id"],
            ["user.id"],
            name="fk_res_cancelled_by",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('scheduled','active','completed','cancelled','failed')",
            name="ck_res_status",
        ),
        sa.CheckConstraint("end_at > start_at", name="ck_res_time_order"),
        sa.CheckConstraint(
            "script_scheduled_start_at IS NULL OR "
            "(script_scheduled_start_at >= start_at AND script_scheduled_start_at < end_at)",
            name="ck_res_script_time",
        ),
        sa.CheckConstraint(
            "gpu_compute_share_pct IS NULL OR "
            "(gpu_compute_share_pct >= 1 AND gpu_compute_share_pct <= 100)",
            name="ck_res_compute_pct",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index(
        "idx_res_gpu_time", "reservation", ["gpu_id", "start_at", "end_at"], unique=False
    )
    op.create_index("idx_res_server_time", "reservation", ["server_id", "start_at"], unique=False)
    op.create_index("idx_res_user_time", "reservation", ["user_id", "start_at"], unique=False)
    op.create_index(
        "idx_res_link_time", "reservation", ["account_link_id", "start_at"], unique=False
    )
    op.create_index("idx_res_status_start", "reservation", ["status", "start_at"], unique=False)
    op.create_index(
        "idx_res_status_script_start",
        "reservation",
        ["status", "script_scheduled_start_at"],
        unique=False,
    )
    op.create_index("idx_res_group", "reservation", ["group_id"], unique=False)


def downgrade() -> None:
    op.drop_table("reservation")
