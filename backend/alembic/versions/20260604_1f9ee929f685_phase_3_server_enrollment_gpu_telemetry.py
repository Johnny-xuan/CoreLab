"""phase 3 server enrollment + gpu telemetry

Revision ID: 1f9ee929f685
Revises: 2b7efc3460f6
Create Date: 2026-06-04 06:15:05.351741+00:00

Creates the five Phase 3 tables — server, enrollment_token, gpu,
server_admin_grant, agent_capability — matching docs/02-data-model.md
§5.5 / §5.6 / §5.7 / §5.8 / §5.17.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "1f9ee929f685"  # pragma: allowlist secret
down_revision: str | None = "2b7efc3460f6"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "server",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("lab_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("hostname", mysql.VARCHAR(255), nullable=False),
        sa.Column("display_name", mysql.VARCHAR(100), nullable=True),
        sa.Column("ip_address", mysql.VARCHAR(45), nullable=True),
        sa.Column("os_info", mysql.VARCHAR(255), nullable=True),
        sa.Column("kernel_version", mysql.VARCHAR(64), nullable=True),
        sa.Column("cpu_model", mysql.VARCHAR(255), nullable=True),
        sa.Column("cpu_cores", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("memory_total_mb", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("agent_version", mysql.VARCHAR(32), nullable=True),
        sa.Column("agent_token_hash", mysql.CHAR(60), nullable=True),
        sa.Column("status", mysql.VARCHAR(32), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("last_heartbeat_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column(
            "max_reservation_hours",
            mysql.INTEGER(unsigned=True),
            server_default=sa.text("24"),
            nullable=True,
        ),
        sa.Column(
            "is_active", mysql.TINYINT(display_width=1), server_default=sa.text("1"), nullable=False
        ),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("approved_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("approved_by_user_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["lab_id"], ["lab.id"], name="fk_server_lab", onupdate="CASCADE", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user.id"],
            name="fk_server_creator",
            onupdate="CASCADE",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"],
            ["user.id"],
            name="fk_server_approver",
            onupdate="CASCADE",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("lab_id", "hostname", name="uq_server_lab_hostname"),
        sa.CheckConstraint(
            "status IN ('pending', 'online', 'offline', 'maintenance')", name="ck_server_status"
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("idx_server_status", "server", ["status", "last_heartbeat_at"])

    op.create_table(
        "enrollment_token",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("lab_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("token_hash", mysql.CHAR(64), nullable=False),
        sa.Column("expected_hostname_pattern", mysql.VARCHAR(255), nullable=True),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("used_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("used_by_server_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["lab_id"],
            ["lab.id"],
            name="fk_enroll_token_lab",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["used_by_server_id"],
            ["server.id"],
            name="fk_enroll_token_server",
            onupdate="CASCADE",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user.id"],
            name="fk_enroll_token_creator",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("token_hash", name="uq_enroll_token_hash"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("idx_enroll_token_lab_used", "enrollment_token", ["lab_id", "used_at"])

    op.create_table(
        "gpu",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("server_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("gpu_index", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("uuid", mysql.VARCHAR(64), nullable=True),
        sa.Column("model", mysql.VARCHAR(100), nullable=True),
        sa.Column("memory_total_mb", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("compute_capability", mysql.VARCHAR(8), nullable=True),
        sa.Column("util_pct", mysql.TINYINT(unsigned=True), nullable=True),
        sa.Column("memory_used_mb", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("temperature_c", mysql.SMALLINT(unsigned=True), nullable=True),
        sa.Column("power_w", mysql.SMALLINT(unsigned=True), nullable=True),
        sa.Column("process_snapshot", mysql.JSON(), nullable=True),
        sa.Column("last_updated_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column(
            "is_active", mysql.TINYINT(display_width=1), server_default=sa.text("1"), nullable=False
        ),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["server.id"],
            name="fk_gpu_server",
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("server_id", "gpu_index", name="uq_gpu_server_index"),
        sa.CheckConstraint("util_pct IS NULL OR util_pct <= 100", name="ck_gpu_util"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("idx_gpu_server", "gpu", ["server_id", "is_active"])

    op.create_table(
        "server_admin_grant",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("server_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("granted_by_user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column(
            "granted_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column("notes", mysql.VARCHAR(255), nullable=True),
        sa.Column(
            "is_active", mysql.TINYINT(display_width=1), server_default=sa.text("1"), nullable=False
        ),
        sa.Column(
            "is_active_uk",
            mysql.TINYINT(display_width=1),
            sa.Computed("IF(is_active=1,1,NULL)", persisted=True),
            nullable=True,
        ),
        sa.Column("revoked_by_user_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("revoked_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name="fk_grant_user", onupdate="CASCADE", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["server.id"],
            name="fk_grant_server",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_user_id"],
            ["user.id"],
            name="fk_grant_granter",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"],
            ["user.id"],
            name="fk_grant_revoker",
            onupdate="CASCADE",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "user_id", "server_id", "is_active_uk", name="uq_grant_user_server_active"
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("idx_grant_server", "server_admin_grant", ["server_id", "is_active"])
    op.create_index("idx_grant_user", "server_admin_grant", ["user_id", "is_active"])

    op.create_table(
        "agent_capability",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("server_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("capability_key", mysql.VARCHAR(64), nullable=False),
        sa.Column("is_enabled", mysql.TINYINT(display_width=1), nullable=False),
        sa.Column(
            "is_dangerous",
            mysql.TINYINT(display_width=1),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("notes", mysql.VARCHAR(500), nullable=True),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column("updated_by_user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["server.id"],
            name="fk_cap_server",
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["user.id"],
            name="fk_cap_updater",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("server_id", "capability_key", name="uq_capability_server_key"),
        sa.CheckConstraint(
            "is_dangerous = 0 OR is_enabled = 0 OR (notes IS NOT NULL AND CHAR_LENGTH(notes) >= 10)",
            name="ck_cap_notes_required",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )


def downgrade() -> None:
    # Reverse FK-dependency order so drop_table can cascade cleanly.
    op.drop_table("agent_capability")
    op.drop_table("server_admin_grant")
    op.drop_table("gpu")
    op.drop_table("enrollment_token")
    op.drop_table("server")
