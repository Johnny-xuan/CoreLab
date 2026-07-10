"""phase 8 alert_event table

Revision ID: 5b9e3d8c2f4a
Revises: 4a8d2c9f1e7b
Create Date: 2026-06-05 04:30:00.000000+00:00

Adds the ``alert_event`` table per docs/02 §5.16 — health alerts
(gpu.hang / gpu.oom / gpu.temp_high / agent.offline / ...) produced by
agent compliance violations + backend health watchdogs. Backed by the
P7 ``/ws/user`` hub for live ``alert.new`` push.

Application-layer dedup: ``(server_id, gpu_id, event_type)`` within 1
hour skips creating a fresh row (docs/02 §5.16 line 1341).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "5b9e3d8c2f4a"  # pragma: allowlist secret
down_revision: str | None = "4a8d2c9f1e7b"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alert_event",
        sa.Column("id", mysql.BIGINT(unsigned=True), nullable=False, autoincrement=True),
        sa.Column("server_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("gpu_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("reservation_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("event_type", mysql.VARCHAR(32), nullable=False),
        sa.Column("severity", mysql.VARCHAR(16), nullable=False),
        sa.Column("payload", mysql.JSON, nullable=True),
        sa.Column("notified_user_ids", mysql.JSON, nullable=True),
        sa.Column(
            "is_resolved",
            mysql.TINYINT(1),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("resolved_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("resolved_by_user_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("resolution_note", mysql.VARCHAR(500), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["server.id"],
            name="fk_alert_server",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["gpu_id"],
            ["gpu.id"],
            name="fk_alert_gpu",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            ["reservation.id"],
            name="fk_alert_res",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"],
            ["user.id"],
            name="fk_alert_resolver",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        sa.CheckConstraint(
            "severity IN ('info','warn','critical')",
            name="ck_alert_severity",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index(
        "idx_alert_server_unresolved",
        "alert_event",
        ["server_id", "is_resolved", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_alert_gpu",
        "alert_event",
        ["gpu_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_alert_type_time",
        "alert_event",
        ["event_type", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_alert_type_time", table_name="alert_event")
    op.drop_index("idx_alert_gpu", table_name="alert_event")
    op.drop_index("idx_alert_server_unresolved", table_name="alert_event")
    op.drop_table("alert_event")
