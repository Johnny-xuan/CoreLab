"""AgentPolicy — per-server soft-tuning for compliance reactions.

Pairs with :class:`AgentCapability` (hard gate, "agent allowed to do X?")
to express "when agent detects X, what should it do" — 8 policy_key x
4 severity (log_only / notify / warn / auto_kill). Co-invariant: if
``severity='auto_kill'`` but the matching capability is off, agent
silently downgrades to ``warn`` (docs/04 §9.7.4).

Phase 9 / FU-37: ``threshold_value`` is now a JSON column carrying a
per-policy_key payload (see ``THRESHOLD_SCHEMAS`` in the service for
the validated shapes). ``gpu_hang`` carries
``{util_zero_seconds, mem_floor_mb}``; ``memory_overuse`` /
``gpu_temp_high`` carry ``{value, unit}``; the remaining 5 keys store
``NULL`` (no threshold).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.mysql import INTEGER, JSON, TINYINT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import FK_TYPE, PK_TYPE, TIMESTAMP_NOW, TIMESTAMP_TYPE, Base


class AgentPolicy(Base):
    __tablename__ = "agent_policy"
    __table_args__ = (
        UniqueConstraint("server_id", "policy_key", name="uq_policy_server_key"),
        CheckConstraint(
            "severity IN ('log_only','notify','warn','auto_kill')",
            name="ck_policy_severity",
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
        ForeignKey(
            "server.id",
            name="fk_policy_server",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        nullable=False,
    )
    policy_key: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    enabled: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default="1")
    severity: Mapped[str] = mapped_column(VARCHAR(16), nullable=False)
    threshold_value: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    grace_period_seconds: Mapped[int | None] = mapped_column(INTEGER(unsigned=True), nullable=True)
    notify_admin: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default="1")
    notes: Mapped[str | None] = mapped_column(VARCHAR(500), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE,
        nullable=False,
        server_default=TIMESTAMP_NOW,
        server_onupdate=TIMESTAMP_NOW,
    )
    updated_by_user_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey(
            "user.id",
            name="fk_policy_updater",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        nullable=False,
    )
