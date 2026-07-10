"""AlertEvent — health alerts pushed live through ``/ws/user``.

Producer side lives in :mod:`alert_service`; consumer side is the bell
hub on the frontend. Pattern is persist-then-push at-least-once (P8-11
sibling of the Phase 7 notification contract).

App-layer dedup keeps the same ``(server_id, gpu_id, event_type)``
triple from firing more than once per hour (docs/02 §5.16 line 1341).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, text
from sqlalchemy.dialects.mysql import JSON, TINYINT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import FK_TYPE, PK_TYPE, TIMESTAMP_NOW, TIMESTAMP_TYPE, Base


class AlertEvent(Base):
    __tablename__ = "alert_event"
    __table_args__ = (
        Index(
            "idx_alert_server_unresolved",
            "server_id",
            "is_resolved",
            text("created_at DESC"),
        ),
        Index("idx_alert_gpu", "gpu_id", text("created_at DESC")),
        Index("idx_alert_type_time", "event_type", text("created_at DESC")),
        CheckConstraint(
            "severity IN ('info','warn','critical')",
            name="ck_alert_severity",
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
            name="fk_alert_server",
            ondelete="RESTRICT",
            onupdate="CASCADE",
        ),
        nullable=False,
    )
    gpu_id: Mapped[int | None] = mapped_column(
        FK_TYPE,
        ForeignKey(
            "gpu.id",
            name="fk_alert_gpu",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        nullable=True,
    )
    reservation_id: Mapped[int | None] = mapped_column(
        FK_TYPE,
        ForeignKey(
            "reservation.id",
            name="fk_alert_res",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    severity: Mapped[str] = mapped_column(VARCHAR(16), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    notified_user_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    is_resolved: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default="0")
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    resolved_by_user_id: Mapped[int | None] = mapped_column(
        FK_TYPE,
        ForeignKey(
            "user.id",
            name="fk_alert_resolver",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        nullable=True,
    )
    resolution_note: Mapped[str | None] = mapped_column(VARCHAR(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE, nullable=False, server_default=TIMESTAMP_NOW
    )
