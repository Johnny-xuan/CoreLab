"""AccountLinkRequest — user asks an admin to push their SSH key into a PA.

Lightweight workflow: user wants to act-as some Linux account but has
no key in its ``authorized_keys`` yet → submit request → admin
approves → backend asks agent to push the user's active SSH public
key → user then completes SSH challenge to actually establish the link.
Approval is *not* the same as a link — the link only exists once the
user proves key ownership via challenge.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.dialects.mysql import VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import FK_TYPE, PK_TYPE, TIMESTAMP_NOW, TIMESTAMP_TYPE, Base


class AccountLinkRequest(Base):
    __tablename__ = "account_link_request"
    __table_args__ = (
        Index("idx_alr_requester", "requester_user_id", "status"),
        Index("idx_alr_pa_status", "physical_account_id", "status"),
        CheckConstraint(
            "status IN ('pending','approved','denied','withdrawn')", name="ck_alr_status"
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    requester_user_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey("user.id", name="fk_alr_requester", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    physical_account_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey("physical_account.id", name="fk_alr_pa", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(VARCHAR(32), nullable=False, server_default="pending")
    request_note: Mapped[str | None] = mapped_column(VARCHAR(500), nullable=True)
    decided_by_user_id: Mapped[int | None] = mapped_column(
        FK_TYPE,
        ForeignKey("user.id", name="fk_alr_decider", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(VARCHAR(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE, nullable=False, server_default=TIMESTAMP_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE,
        nullable=False,
        server_default=TIMESTAMP_NOW,
        server_onupdate=TIMESTAMP_NOW,
    )
