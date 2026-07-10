"""phase 9 FU-37 agent_policy.threshold_value INT -> JSON

Revision ID: 6c3f4a8d9e2b
Revises: 5b9e3d8c2f4a
Create Date: 2026-06-05 12:30:00.000000+00:00

FU-37 — replace the single ``threshold_value INT`` column with a JSON
column so policies can carry multi-field thresholds. ``gpu_hang`` is
the immediate beneficiary: Phase 8 had ``mem_floor_mb=1024`` hardcoded
in the agent monitor; that constant now lives in the row payload
together with ``util_zero_seconds``. ``memory_overuse`` /
``gpu_temp_high`` move to ``{"value": N, "unit": "..."}`` for
discoverability; the remaining 5 keys carry ``NULL`` (no threshold).

Upgrade strategy: add JSON column, backfill via per-policy_key
``UPDATE``, drop INT column, rename JSON column to ``threshold_value``.
This matches the brief §4.1 plan but uses MySQL ``CHANGE COLUMN``
semantics (alembic ``alter_column(new_column_name=...)`` underneath).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "6c3f4a8d9e2b"  # pragma: allowlist secret
down_revision: str | None = "5b9e3d8c2f4a"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) Stage the new JSON column nullable so backfill can proceed
    #    without touching the existing INT column.
    op.add_column(
        "agent_policy",
        sa.Column("threshold_value_json", mysql.JSON, nullable=True),
    )

    # 2) Generic backfill for the non-special keys (memory_overuse /
    #    gpu_temp_high carry a unit-tagged dict; the 5 "no threshold"
    #    keys stay NULL).
    op.execute(
        """
        UPDATE agent_policy
        SET threshold_value_json = JSON_OBJECT('value', threshold_value, 'unit', 'pct')
        WHERE policy_key = 'memory_overuse' AND threshold_value IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE agent_policy
        SET threshold_value_json = JSON_OBJECT('value', threshold_value, 'unit', 'celsius')
        WHERE policy_key = 'gpu_temp_high' AND threshold_value IS NOT NULL
        """
    )

    # 3) gpu_hang special: pull mem_floor_mb out of the hardcoded
    #    constant in the agent so admins can tune it per-server.
    op.execute(
        """
        UPDATE agent_policy
        SET threshold_value_json = JSON_OBJECT(
            'util_zero_seconds', COALESCE(threshold_value, 600),
            'mem_floor_mb', 1024
        )
        WHERE policy_key = 'gpu_hang'
        """
    )

    # 4) Drop the legacy INT column and promote the JSON one.
    op.drop_column("agent_policy", "threshold_value")
    op.alter_column(
        "agent_policy",
        "threshold_value_json",
        new_column_name="threshold_value",
        existing_type=mysql.JSON,
        existing_nullable=True,
    )


def downgrade() -> None:
    # Reverse: read the ``value`` key back into INT (the gpu_hang
    # downgrade therefore loses ``mem_floor_mb`` — that information was
    # not representable in v0.2; downgraders accept the loss).
    op.add_column(
        "agent_policy",
        sa.Column("threshold_value_int", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        UPDATE agent_policy
        SET threshold_value_int = CAST(JSON_EXTRACT(threshold_value, '$.value') AS UNSIGNED)
        WHERE policy_key IN ('memory_overuse', 'gpu_temp_high')
          AND threshold_value IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE agent_policy
        SET threshold_value_int = CAST(JSON_EXTRACT(threshold_value, '$.util_zero_seconds') AS UNSIGNED)
        WHERE policy_key = 'gpu_hang' AND threshold_value IS NOT NULL
        """
    )
    op.drop_column("agent_policy", "threshold_value")
    op.alter_column(
        "agent_policy",
        "threshold_value_int",
        new_column_name="threshold_value",
        existing_type=sa.Integer(),
        existing_nullable=True,
    )
