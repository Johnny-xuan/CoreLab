"""phase 9 alert_event.event_type VARCHAR(32) -> VARCHAR(64)

Revision ID: 7d4e5f8c2a1b
Revises: 6c3f4a8d9e2b
Create Date: 2026-06-05 12:45:00.000000+00:00

Phase 9 / FU-38 — agent-pushed compliance violations land
``event_type='compliance.<policy_key>'``. The longest policy_key is
``preempt_others_reservation`` (26 chars), so the prefixed event_type
needs 37 chars; the Phase 8 VARCHAR(32) cap overflows. Bump to 64
to match action / target_type lengths in audit_log.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "7d4e5f8c2a1b"  # pragma: allowlist secret
down_revision: str | None = "6c3f4a8d9e2b"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "alert_event",
        "event_type",
        existing_type=mysql.VARCHAR(32),
        type_=mysql.VARCHAR(64),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "alert_event",
        "event_type",
        existing_type=mysql.VARCHAR(64),
        type_=mysql.VARCHAR(32),
        existing_nullable=False,
    )
