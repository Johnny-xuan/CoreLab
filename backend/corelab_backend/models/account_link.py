"""AccountLink — M:N between platform users and Linux accounts on servers.

The single most important index in CoreLab:
- Reverse lookup (agent → backend): "which platform users own this
  Linux account I see using a GPU?" — drives compliance monitoring.
- Act-as (backend → agent): only ``source != 'admin_declared'`` rows
  can be used to ``sudo -u <linux_user>`` (run a reservation script,
  push an SSH key). admin_declared rows are visible to lookups but
  rejected by the act-as service helper.

``is_active_uk`` partial-UNIQUE trick: revoke → write a new row →
history preserved.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Computed, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import JSON, TINYINT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import FK_TYPE, PK_TYPE, TIMESTAMP_NOW, TIMESTAMP_TYPE, Base


class AccountLink(Base):
    __tablename__ = "account_link"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "physical_account_id", "is_active_uk", name="uq_link_user_pa_active"
        ),
        Index("idx_link_user_active", "user_id", "is_active"),
        Index("idx_link_pa_active", "physical_account_id", "is_active"),
        CheckConstraint(
            "source IN ('ssh_challenge','password_pam','admin_prepared_then_ssh','admin_declared')",
            name="ck_link_source",
        ),
        CheckConstraint(
            # Phase 9 / FU-21 — 'upgraded_to_verified' added so the
            # admin_declared -> ssh_challenge upgrade has a dedicated
            # marker instead of overloading 'self' (docs/04 §10 line
            # 781). Migration 8e5c2f9a3d1b alters the live DDL.
            "revoke_reason IS NULL OR "
            "revoke_reason IN ('self','admin_force','user_disabled',"
            "'pa_disabled','upgraded_to_verified')",
            name="ck_link_revoke_reason",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey("user.id", name="fk_link_user", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    physical_account_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey(
            "physical_account.id", name="fk_link_pa", ondelete="RESTRICT", onupdate="CASCADE"
        ),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    proof_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    established_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE, nullable=False, server_default=TIMESTAMP_NOW
    )
    established_by_user_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey(
            "user.id", name="fk_link_established_by", ondelete="RESTRICT", onupdate="CASCADE"
        ),
        nullable=False,
    )
    is_active: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default="1")
    is_active_uk: Mapped[int | None] = mapped_column(
        TINYINT(1), Computed("IF(is_active=1,1,NULL)", persisted=True), nullable=True
    )
    revoked_by_user_id: Mapped[int | None] = mapped_column(
        FK_TYPE,
        ForeignKey("user.id", name="fk_link_revoker", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(VARCHAR(32), nullable=True)
