"""audit_log runtime immutability triggers

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-07-04 20:36:00.000000+08:00

The runtime app user has schema-wide UPDATE for ordinary soft-delete and
state-transition tables. Enforce audit append-only semantics at the table
boundary so SQL injection, future endpoints, or maintenance scripts running
as ``corelab_app`` cannot mutate audit rows.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d5e6f7a8b9c0"  # pragma: allowlist secret
down_revision: str | None = "c4d5e6f7a8b9"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TRIGGER trg_audit_log_no_update_for_app_user
        BEFORE UPDATE ON audit_log
        FOR EACH ROW
        BEGIN
            IF SUBSTRING_INDEX(USER(), '@', 1) = 'corelab_app' THEN
                SIGNAL SQLSTATE '45000'
                    SET MESSAGE_TEXT = 'audit_log is append-only: UPDATE forbidden';
            END IF;
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_log_no_delete_for_app_user
        BEFORE DELETE ON audit_log
        FOR EACH ROW
        BEGIN
            IF SUBSTRING_INDEX(USER(), '@', 1) = 'corelab_app' THEN
                SIGNAL SQLSTATE '45000'
                    SET MESSAGE_TEXT = 'audit_log is append-only: DELETE forbidden';
            END IF;
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_no_delete_for_app_user")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_no_update_for_app_user")
