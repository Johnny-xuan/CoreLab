"""AgentCapability — per-server hard-gate switch for agent operations.

Dangerous capabilities (gpu.kill_process / linux.useradd / linux.userdel)
ship disabled and require a ``notes`` string of at least 10 characters
before they can be turned on. Enforced both at the API layer (422) and
at the DB layer via a CHECK constraint so misconfigured callers can't
bypass.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.mysql import TINYINT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import FK_TYPE, PK_TYPE, TIMESTAMP_NOW, TIMESTAMP_TYPE, Base


class AgentCapability(Base):
    __tablename__ = "agent_capability"
    __table_args__ = (
        UniqueConstraint("server_id", "capability_key", name="uq_capability_server_key"),
        CheckConstraint(
            "is_dangerous = 0 OR is_enabled = 0 OR (notes IS NOT NULL AND CHAR_LENGTH(notes) >= 10)",
            name="ck_cap_notes_required",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    server_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey("server.id", name="fk_cap_server", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    capability_key: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    is_enabled: Mapped[int] = mapped_column(TINYINT(1), nullable=False)
    is_dangerous: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default="0")
    notes: Mapped[str | None] = mapped_column(VARCHAR(500), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE,
        nullable=False,
        server_default=TIMESTAMP_NOW,
        server_onupdate=TIMESTAMP_NOW,
    )
    updated_by_user_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey("user.id", name="fk_cap_updater", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
