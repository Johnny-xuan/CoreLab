"""reservation script log bounded tail

Revision ID: c4d5e6f7a8b9
Revises: bc23de45fa67
Create Date: 2026-07-04 00:52:00.000000+08:00

Stores only a recent script output tail in the reservation row. Full logs
remain on the agent host at ``script_log_path``; this column supports the
platform UI's quick read path without turning CoreLab into a log archive.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "c4d5e6f7a8b9"  # pragma: allowlist secret
down_revision: str | None = "bc23de45fa67"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reservation",
        sa.Column("script_log_tail_text", mysql.TEXT, nullable=True),
    )
    op.add_column(
        "reservation",
        sa.Column(
            "script_log_tail_truncated",
            mysql.TINYINT(1),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("reservation", "script_log_tail_truncated")
    op.drop_column("reservation", "script_log_tail_text")
