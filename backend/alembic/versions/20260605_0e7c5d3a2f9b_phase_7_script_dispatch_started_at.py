"""phase 7 reservation script_dispatch_started_at column

Revision ID: 0e7c5d3a2f9b
Revises: 8c52e7af1d3a
Create Date: 2026-06-05 02:00:00.000000+00:00

Adds the ``script_dispatch_started_at`` outbox-pattern column required
by Phase 7 C0 (B 方案 — Worker Catch #1).

Why: Phase 6 left ``_dispatch_due_scripts`` as a stub log. Phase 7 wires
the real ``backend.script.execute`` RPC. The first design (worker
recap Catch #1) tried to rollback ``script_status: running -> NULL`` on
RPC failure, which violates the Phase 6 ``_ALLOWED_SCRIPT_TRANSITIONS``
table (running has no edge back to NULL) and would also leave the row
stuck on ``running`` forever if the backend crashed between the
SERIALIZABLE commit and ``asyncio.create_task(_dispatch_one)``.

The B 方案 fix is a new column that marks "scheduler already set
``script_status='running'`` and dispatched the RPC, but the agent has
not yet acked". The scheduler sets it inside the SERIALIZABLE tick
alongside ``script_status='running'``; the lifecycle handler clears it
when ``agent.script.started`` arrives; a 4th tick action
(``_retry_stuck_dispatches``) re-fires or marks failed when the column
has stayed non-NULL for more than 60 s. Reservation status never
rolls back, so the Phase 6 invariant table stays untouched.

No backfill is needed — every existing row pre-dates Phase 7
dispatching so the column is harmlessly NULL.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0e7c5d3a2f9b"  # pragma: allowlist secret
down_revision: str | None = "8c52e7af1d3a"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reservation",
        sa.Column(
            "script_dispatch_started_at",
            mysql.DATETIME(fsp=6),
            nullable=True,
            comment=(
                "Phase 7 — set when scheduler marks running, "
                "cleared by agent script.started ack; watchdog reaps after 60s."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("reservation", "script_dispatch_started_at")
