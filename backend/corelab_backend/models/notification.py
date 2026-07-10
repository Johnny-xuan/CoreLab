"""Phase 7 — ``notification`` table (docs/02 §5.14).

User-facing notifications shown in the bell dropdown + pushed live
through ``/ws/user``. Created by ``notification_service.create_notification``
from reservation lifecycle transitions, link-prepared events and the
Phase 8 alert system later on.

Schema follows docs/02 §5.14 verbatim (field list, indexes, CHECK,
FK ON DELETE CASCADE because notifications are scoped to the
recipient — soft-disabling the user removes the rows).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.dialects.mysql import JSON, TEXT, TINYINT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import FK_TYPE, PK_TYPE, TIMESTAMP_NOW, TIMESTAMP_TYPE, Base


class Notification(Base):
    __tablename__ = "notification"
    __table_args__ = (
        Index(
            "idx_notif_recipient_unread",
            "recipient_user_id",
            "is_read",
            "created_at",
        ),
        Index("idx_notif_created", "created_at"),
        CheckConstraint(
            "severity IN ('info','warn','error')",
            name="ck_notif_severity",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    recipient_user_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey(
            "user.id",
            name="fk_notif_recipient",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    severity: Mapped[str] = mapped_column(VARCHAR(16), nullable=False, server_default="info")
    title: Mapped[str] = mapped_column(VARCHAR(255), nullable=False)
    body: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    cta_url: Mapped[str | None] = mapped_column(VARCHAR(500), nullable=True)
    is_read: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default="0")
    read_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE, nullable=False, server_default=TIMESTAMP_NOW
    )
