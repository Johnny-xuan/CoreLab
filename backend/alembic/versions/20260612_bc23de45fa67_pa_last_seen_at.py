"""physical_account.last_seen_at — agent scan freshness marker

Revision ID: bc23de45fa67
Revises: ab12cd34ef56
Create Date: 2026-06-12 12:00:00.000000+00:00

Account discovery (agent.account_scan.report) needs a way to surface
"this Linux account stopped appearing in scans" without auto-deleting
anything: the agent stamps every account it sees, the UI shows the
timestamp, and a human decides what a stale row means (userdel?
rename? agent misconfig?).

NULL = never seen by any scan — true for admin_manual_register rows
on servers whose agent hasn't reconnected yet, and for all rows
created before this revision. No backfill on purpose: pretending we
saw an account at migration time would defeat the column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "bc23de45fa67"  # pragma: allowlist secret
down_revision: str | None = "ab12cd34ef56"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "physical_account",
        sa.Column("last_seen_at", mysql.DATETIME(fsp=6), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("physical_account", "last_seen_at")
