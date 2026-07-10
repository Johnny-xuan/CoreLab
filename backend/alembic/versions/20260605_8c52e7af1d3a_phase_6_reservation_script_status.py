"""phase 6 reservation script_status column + state machine

Revision ID: 8c52e7af1d3a
Revises: ff9621ab430d
Create Date: 2026-06-05 00:05:14.000000+00:00

Adds the ``script_status`` column required by the Phase 6 scheduler +
agent ``script.lifecycle`` flow. Values mirror the enum tracked by
the agent's ``running_scripts`` map: NULL = not yet fired (or the
reservation carries no script), ``running`` = agent reports
``script.started``, ``completed`` = exit 0, ``failed`` = exit != 0,
``killed`` = agent terminated it (user/admin cancel via
``rpc.cancel_script`` or max_runtime grace).

Reservation.status itself stays the existing 5-value enum
(scheduled/active/completed/cancelled/failed) — that constraint is
already enforced by the Phase 5 ``ck_res_status``. The new
``ck_res_script_status`` is a separate constraint that just keeps the
script-side enum honest.

No reservation rows pre-date this migration in any environment
(Phase 5 only writes ``status='scheduled'`` and cancels), so the
column is added NULL-default without a backfill.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "8c52e7af1d3a"  # pragma: allowlist secret
down_revision: str | None = "ff9621ab430d"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reservation",
        sa.Column("script_status", mysql.VARCHAR(16), nullable=True),
    )
    op.create_check_constraint(
        "ck_res_script_status",
        "reservation",
        "script_status IS NULL OR script_status IN ('running','completed','failed','killed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_res_script_status", "reservation", type_="check")
    op.drop_column("reservation", "script_status")
