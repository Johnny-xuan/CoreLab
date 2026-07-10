"""phase 2 identity tables

Revision ID: d00f5df9d2ff
Revises:
Create Date: 2026-06-04 03:01:30.180803+00:00

Creates the five identity tables that back Phase 2 — auth, setup
wizard, users, ssh keys, and audit log. DDL matches docs/02-data-model.md
§5.1 / §5.2 / §5.3 / §5.4 / §5.15 verbatim.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "d00f5df9d2ff"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lab",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("name", mysql.VARCHAR(100), nullable=False),
        sa.Column("slug", mysql.VARCHAR(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_lab_slug"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )

    op.create_table(
        "user",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("lab_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("username", mysql.VARCHAR(64), nullable=False),
        sa.Column("email", mysql.VARCHAR(255), nullable=False),
        sa.Column("password_hash", mysql.CHAR(60), nullable=True),
        sa.Column("display_name", mysql.VARCHAR(100), nullable=False),
        sa.Column("role", mysql.VARCHAR(32), server_default=sa.text("'user'"), nullable=False),
        sa.Column(
            "is_active", mysql.TINYINT(display_width=1), server_default=sa.text("1"), nullable=False
        ),
        sa.Column("last_login_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["lab_id"], ["lab.id"], name="fk_user_lab", onupdate="CASCADE", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user.id"],
            name="fk_user_creator",
            onupdate="CASCADE",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("lab_id", "username", name="uq_user_lab_username"),
        sa.UniqueConstraint("lab_id", "email", name="uq_user_lab_email"),
        sa.CheckConstraint("role IN ('user', 'lab_admin')", name="ck_user_role"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("idx_user_is_active", "user", ["is_active"])

    op.create_table(
        "setup_token",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("token_hash", mysql.CHAR(64), nullable=False),
        sa.Column("purpose", mysql.VARCHAR(32), nullable=False),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("used_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name="fk_setup_token_user",
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user.id"],
            name="fk_setup_token_creator",
            onupdate="CASCADE",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("token_hash", name="uq_setup_token_hash"),
        sa.CheckConstraint(
            "purpose IN ('activation', 'password_reset')", name="ck_setup_token_purpose"
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("idx_setup_token_user", "setup_token", ["user_id", "used_at"])

    op.create_table(
        "ssh_public_key",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("fingerprint_sha256", mysql.CHAR(47), nullable=False),
        sa.Column("key_type", mysql.VARCHAR(32), nullable=False),
        sa.Column("comment", mysql.VARCHAR(255), nullable=True),
        sa.Column(
            "is_active", mysql.TINYINT(display_width=1), server_default=sa.text("1"), nullable=False
        ),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column("disabled_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name="fk_ssh_key_user",
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", "fingerprint_sha256", name="uq_ssh_key_user_fp"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("idx_ssh_key_fp", "ssh_public_key", ["fingerprint_sha256"])

    op.create_table(
        "audit_log",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("lab_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("actor_user_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("actor_session_id", mysql.CHAR(36), nullable=True),
        sa.Column("action", mysql.VARCHAR(64), nullable=False),
        sa.Column("target_type", mysql.VARCHAR(32), nullable=True),
        sa.Column("target_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("target_lab_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("target_server_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("payload", mysql.JSON(), nullable=True),
        sa.Column("ip_address", mysql.VARCHAR(45), nullable=True),
        sa.Column("user_agent", mysql.VARCHAR(255), nullable=True),
        sa.Column("result", mysql.VARCHAR(16), server_default=sa.text("'ok'"), nullable=False),
        sa.Column("error_message", mysql.VARCHAR(500), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["lab_id"], ["lab.id"], name="fk_audit_lab", onupdate="CASCADE", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["user.id"],
            name="fk_audit_actor",
            onupdate="CASCADE",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("result IN ('ok', 'denied', 'error')", name="ck_audit_result"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("idx_audit_actor_time", "audit_log", ["actor_user_id", "created_at"])
    op.create_index("idx_audit_target", "audit_log", ["target_type", "target_id", "created_at"])
    op.create_index("idx_audit_action_time", "audit_log", ["action", "created_at"])
    op.create_index("idx_audit_server_time", "audit_log", ["target_server_id", "created_at"])
    op.create_index("idx_audit_created", "audit_log", ["created_at"])


def downgrade() -> None:
    # Drop in reverse FK-dependency order. drop_table cascades indexes
    # and constraints owned by the table; cross-table FKs (audit_log,
    # setup_token, ssh_public_key all reference user; user references
    # lab) must therefore come down first.
    op.drop_table("audit_log")
    op.drop_table("ssh_public_key")
    op.drop_table("setup_token")
    op.drop_table("user")
    op.drop_table("lab")
