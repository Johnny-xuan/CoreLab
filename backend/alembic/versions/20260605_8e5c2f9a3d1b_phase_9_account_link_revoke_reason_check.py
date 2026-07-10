"""phase 9 account_link.revoke_reason extend with 'upgraded_to_verified'

Revision ID: 8e5c2f9a3d1b
Revises: 7d4e5f8c2a1b
Create Date: 2026-06-05 13:00:00.000000+00:00

Phase 9 / FU-21:

* Phase 4 ``ck_link_revoke_reason`` enumerated 4 values
  ('self','admin_force','user_disabled','pa_disabled'). Phase 4
  upgrade flow had to overload ``'self'`` for the admin_declared ->
  ssh_challenge upgrade because no dedicated reason existed.
* Phase 9 adds ``'upgraded_to_verified'`` (docs/04 §10 line 781) and
  swaps the upgrade flow to use it.

MySQL does not support ALTER CHECK in place: drop + re-add. Legacy
rows that carry the historical ``'self'`` upgrade marker stay valid
because ``'self'`` remains in the new enum.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "8e5c2f9a3d1b"  # pragma: allowlist secret
down_revision: str | None = "7d4e5f8c2a1b"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_NEW_CHECK_EXPR = (
    "revoke_reason IS NULL OR revoke_reason IN ("
    "'self', 'admin_force', 'user_disabled', 'pa_disabled', "
    "'upgraded_to_verified'"
    ")"
)

_OLD_CHECK_EXPR = (
    "revoke_reason IS NULL OR revoke_reason IN ("
    "'self', 'admin_force', 'user_disabled', 'pa_disabled'"
    ")"
)


def upgrade() -> None:
    op.drop_constraint("ck_link_revoke_reason", "account_link", type_="check")
    op.create_check_constraint(
        "ck_link_revoke_reason",
        "account_link",
        _NEW_CHECK_EXPR,
    )


def downgrade() -> None:
    # Restore the Phase 4 constraint; any row carrying the new value
    # would fail the old CHECK, so flip them back to 'self' first.
    op.execute(
        "UPDATE account_link SET revoke_reason = 'self' "
        "WHERE revoke_reason = 'upgraded_to_verified'"
    )
    op.drop_constraint("ck_link_revoke_reason", "account_link", type_="check")
    op.create_check_constraint(
        "ck_link_revoke_reason",
        "account_link",
        _OLD_CHECK_EXPR,
    )
