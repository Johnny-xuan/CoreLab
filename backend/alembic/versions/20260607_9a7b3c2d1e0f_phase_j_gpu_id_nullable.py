"""phase J reservation.gpu_id nullable + ck_res_gpu_or_script

Revision ID: 9a7b3c2d1e0f
Revises: 8e5c2f9a3d1b
Create Date: 2026-06-07 12:00:00.000000+00:00

Phase J turns ``reservation`` into the unified "task" entity:

  ┌──────────────────────────────────────────────────────────────┐
  │ Mode 1  ──  gpu_id NOT NULL, script NULL   = 纯占 GPU         │
  │ Mode 2  ──  gpu_id NOT NULL, script NOT NULL = 预约 + 脚本    │
  │ Mode 3  ──  gpu_id NULL    , script NOT NULL = 纯定时任务     │
  │ (空+空)──  gpu_id NULL    , script NULL    = 禁止             │
  └──────────────────────────────────────────────────────────────┘

Changes:
  1. ``reservation.gpu_id`` → nullable
     (FK kept; MySQL InnoDB skips FK validation when the column is NULL.)

The "gpu_id IS NOT NULL OR script IS NOT NULL" invariant is enforced
at the service layer (reservation_service.create) instead of via a
DB CHECK constraint — MySQL refuses CHECK constraints on columns that
participate in FK referential actions (error 3823), and ON DELETE
RESTRICT on fk_res_gpu makes gpu_id one such column. Service-layer
validation is fine here because every code path that inserts a
reservation row already goes through reservation_service.

The Mode 3 path bypasses GPU conflict detection in reservation_service
and uses ``script_scheduled_start_at`` as its trigger instead of the
reservation lifecycle's scheduled→active transition.

No backfill — every existing row has gpu_id NOT NULL by the old schema
constraint, so the upgrade is a pure NULL-relaxation.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "9a7b3c2d1e0f"  # pragma: allowlist secret
down_revision: str | None = "8e5c2f9a3d1b"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Relax gpu_id to nullable. FK stays; MySQL InnoDB only validates
    # non-NULL values.
    op.alter_column(
        "reservation",
        "gpu_id",
        existing_type=mysql.BIGINT(unsigned=True),
        nullable=True,
        existing_nullable=False,
        existing_comment=None,
    )


def downgrade() -> None:
    # Restore NOT NULL. Note: if Mode 3 rows exist (gpu_id IS NULL with
    # script NOT NULL), this will fail. Downgrade is for dev rollback;
    # production rollback would need a backfill / row-delete first.
    op.alter_column(
        "reservation",
        "gpu_id",
        existing_type=mysql.BIGINT(unsigned=True),
        nullable=False,
        existing_nullable=True,
    )
