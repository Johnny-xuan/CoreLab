"""phase 4 physical accounts + account links

Revision ID: 68e16808e68d
Revises: 1f9ee929f685
Create Date: 2026-06-04 09:39:25.317287+00:00

Creates the four Phase 4 tables — physical_account,
authorized_key_entry, account_link, account_link_request — matching
docs/02-data-model.md §5.9 / §5.10 / §5.11 / §5.19.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "68e16808e68d"  # pragma: allowlist secret
down_revision: str | None = "1f9ee929f685"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "physical_account",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("server_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("linux_username", mysql.VARCHAR(32), nullable=False),
        sa.Column("uid", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("gid", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("home_directory", mysql.VARCHAR(255), nullable=True),
        sa.Column("default_shell", mysql.VARCHAR(100), nullable=True),
        sa.Column("source", mysql.VARCHAR(32), nullable=False),
        sa.Column(
            "is_active",
            mysql.TINYINT(display_width=1),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "is_active_uk",
            mysql.TINYINT(display_width=1),
            sa.Computed("IF(is_active=1,1,NULL)", persisted=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("notes", mysql.VARCHAR(255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["server.id"],
            name="fk_pa_server",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user.id"],
            name="fk_pa_creator",
            onupdate="CASCADE",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "server_id", "linux_username", "is_active_uk", name="uq_pa_server_user_active"
        ),
        sa.CheckConstraint(
            "source IN ('agent_created','discovered_scan','admin_manual_register')",
            name="ck_pa_source",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("idx_pa_server", "physical_account", ["server_id", "is_active"])

    op.create_table(
        "authorized_key_entry",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("physical_account_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("ssh_public_key_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("pushed_by_user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("pushed_for_user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column(
            "pushed_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            mysql.TINYINT(display_width=1),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("removed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("removed_by_user_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["physical_account_id"],
            ["physical_account.id"],
            name="fk_ake_pa",
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ssh_public_key_id"],
            ["ssh_public_key.id"],
            name="fk_ake_key",
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pushed_by_user_id"],
            ["user.id"],
            name="fk_ake_pusher",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["pushed_for_user_id"],
            ["user.id"],
            name="fk_ake_for_user",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["removed_by_user_id"],
            ["user.id"],
            name="fk_ake_remover",
            onupdate="CASCADE",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("physical_account_id", "ssh_public_key_id", name="uq_ake_pa_key"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index(
        "idx_ake_pa_active", "authorized_key_entry", ["physical_account_id", "is_active"]
    )
    op.create_index("idx_ake_user", "authorized_key_entry", ["pushed_for_user_id"])

    op.create_table(
        "account_link",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("physical_account_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("source", mysql.VARCHAR(32), nullable=False),
        sa.Column("proof_evidence", mysql.JSON(), nullable=False),
        sa.Column(
            "established_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column("established_by_user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column(
            "is_active",
            mysql.TINYINT(display_width=1),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "is_active_uk",
            mysql.TINYINT(display_width=1),
            sa.Computed("IF(is_active=1,1,NULL)", persisted=True),
            nullable=True,
        ),
        sa.Column("revoked_by_user_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("revoked_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("revoke_reason", mysql.VARCHAR(32), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name="fk_link_user",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["physical_account_id"],
            ["physical_account.id"],
            name="fk_link_pa",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["established_by_user_id"],
            ["user.id"],
            name="fk_link_established_by",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"],
            ["user.id"],
            name="fk_link_revoker",
            onupdate="CASCADE",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "user_id", "physical_account_id", "is_active_uk", name="uq_link_user_pa_active"
        ),
        sa.CheckConstraint(
            "source IN ('ssh_challenge','password_pam','admin_prepared_then_ssh','admin_declared')",
            name="ck_link_source",
        ),
        sa.CheckConstraint(
            "revoke_reason IS NULL OR "
            "revoke_reason IN ('self','admin_force','user_disabled','pa_disabled')",
            name="ck_link_revoke_reason",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("idx_link_user_active", "account_link", ["user_id", "is_active"])
    op.create_index("idx_link_pa_active", "account_link", ["physical_account_id", "is_active"])

    op.create_table(
        "account_link_request",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("requester_user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("physical_account_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("status", mysql.VARCHAR(32), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("request_note", mysql.VARCHAR(500), nullable=True),
        sa.Column("decided_by_user_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("decided_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("decision_note", mysql.VARCHAR(500), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["requester_user_id"],
            ["user.id"],
            name="fk_alr_requester",
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["physical_account_id"],
            ["physical_account.id"],
            name="fk_alr_pa",
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["user.id"],
            name="fk_alr_decider",
            onupdate="CASCADE",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('pending','approved','denied','withdrawn')", name="ck_alr_status"
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("idx_alr_requester", "account_link_request", ["requester_user_id", "status"])
    op.create_index("idx_alr_pa_status", "account_link_request", ["physical_account_id", "status"])


def downgrade() -> None:
    # Reverse FK-dependency order so drop_table cascades cleanly.
    op.drop_table("account_link_request")
    op.drop_table("account_link")
    op.drop_table("authorized_key_entry")
    op.drop_table("physical_account")
