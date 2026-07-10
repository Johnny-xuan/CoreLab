"""fix ssh fingerprint length to 50

Revision ID: 2b7efc3460f6
Revises: d00f5df9d2ff
Create Date: 2026-06-04 03:23:01.341300+00:00

The doc spec (docs/02-data-model.md §5.4) declares CHAR(47), but
``ssh-keygen -l -f`` actually emits ``SHA256:<43-char-base64>`` = 50
characters total. CHAR(47) cannot hold a real fingerprint; reported as
a Phase 2 doc nit. This migration widens the column so the activate /
SSH-key endpoints work.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "2b7efc3460f6"  # pragma: allowlist secret
down_revision: str | None = "d00f5df9d2ff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "ssh_public_key",
        "fingerprint_sha256",
        existing_type=mysql.CHAR(47),
        type_=mysql.CHAR(50),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "ssh_public_key",
        "fingerprint_sha256",
        existing_type=mysql.CHAR(50),
        type_=mysql.CHAR(47),
        existing_nullable=False,
    )
