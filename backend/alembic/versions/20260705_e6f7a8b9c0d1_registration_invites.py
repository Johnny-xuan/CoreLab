"""registration invites before user creation

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-07-05 17:55:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "e6f7a8b9c0d1"  # pragma: allowlist secret
down_revision: str | None = "d5e6f7a8b9c0"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "registration_invite",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("lab_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("token_hash", mysql.CHAR(64), nullable=False),
        sa.Column("role", mysql.VARCHAR(32), server_default=sa.text("'user'"), nullable=False),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("used_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("used_by_user_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["lab_id"],
            ["lab.id"],
            name="fk_registration_invite_lab",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user.id"],
            name="fk_registration_invite_creator",
            onupdate="CASCADE",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["used_by_user_id"],
            ["user.id"],
            name="fk_registration_invite_used_by",
            onupdate="CASCADE",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("token_hash", name="uq_registration_invite_token_hash"),
        sa.CheckConstraint("role IN ('user', 'lab_admin')", name="ck_registration_invite_role"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("idx_registration_invite_lab", "registration_invite", ["lab_id", "used_at"])


def downgrade() -> None:
    op.drop_index("idx_registration_invite_lab", table_name="registration_invite")
    op.drop_table("registration_invite")
