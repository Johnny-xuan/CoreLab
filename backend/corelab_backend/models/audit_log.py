"""AuditLog — append-only operation audit trail.

Service layer is the only writer; this ORM class deliberately offers no
``update`` or ``delete`` helpers. DB-level enforcement lives in Alembic
triggers that reject runtime ``corelab_app`` UPDATE/DELETE attempts on
this table (invariant #8).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.dialects.mysql import CHAR, JSON, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import FK_TYPE, PK_TYPE, TIMESTAMP_NOW, TIMESTAMP_TYPE, Base


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("idx_audit_actor_time", "actor_user_id", "created_at"),
        Index("idx_audit_target", "target_type", "target_id", "created_at"),
        Index("idx_audit_action_time", "action", "created_at"),
        Index("idx_audit_server_time", "target_server_id", "created_at"),
        Index("idx_audit_created", "created_at"),
        CheckConstraint("result IN ('ok', 'denied', 'error')", name="ck_audit_result"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    lab_id: Mapped[int | None] = mapped_column(
        FK_TYPE,
        ForeignKey("lab.id", name="fk_audit_lab", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        FK_TYPE,
        ForeignKey("user.id", name="fk_audit_actor", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    actor_session_id: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
    action: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(VARCHAR(32), nullable=True)
    target_id: Mapped[int | None] = mapped_column(FK_TYPE, nullable=True)
    target_lab_id: Mapped[int | None] = mapped_column(FK_TYPE, nullable=True)
    target_server_id: Mapped[int | None] = mapped_column(FK_TYPE, nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(VARCHAR(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    result: Mapped[str] = mapped_column(VARCHAR(16), nullable=False, server_default="ok")
    error_message: Mapped[str | None] = mapped_column(VARCHAR(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE, nullable=False, server_default=TIMESTAMP_NOW
    )
