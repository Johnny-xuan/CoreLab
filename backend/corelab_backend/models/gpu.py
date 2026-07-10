"""GPU — a single card on a server.

Deliberate denorm: schema columns (model / memory_total_mb) live next to
the latest telemetry snapshot (util_pct / memory_used_mb / temperature_c
/ process_snapshot JSON). Historical curves are out of scope; a future
``gpu_telemetry`` append-only table would coexist with these latest
columns (kept as a cache).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import INTEGER, JSON, SMALLINT, TINYINT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import FK_TYPE, PK_TYPE, TIMESTAMP_NOW, TIMESTAMP_TYPE, Base


class Gpu(Base):
    __tablename__ = "gpu"
    __table_args__ = (
        UniqueConstraint("server_id", "gpu_index", name="uq_gpu_server_index"),
        Index("idx_gpu_server", "server_id", "is_active"),
        CheckConstraint("util_pct IS NULL OR util_pct <= 100", name="ck_gpu_util"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    server_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey("server.id", name="fk_gpu_server", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    gpu_index: Mapped[int] = mapped_column(INTEGER(unsigned=True), nullable=False)
    uuid: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    model: Mapped[str | None] = mapped_column(VARCHAR(100), nullable=True)
    memory_total_mb: Mapped[int | None] = mapped_column(INTEGER(unsigned=True), nullable=True)
    compute_capability: Mapped[str | None] = mapped_column(VARCHAR(8), nullable=True)
    util_pct: Mapped[int | None] = mapped_column(TINYINT(unsigned=True), nullable=True)
    memory_used_mb: Mapped[int | None] = mapped_column(INTEGER(unsigned=True), nullable=True)
    temperature_c: Mapped[int | None] = mapped_column(SMALLINT(unsigned=True), nullable=True)
    power_w: Mapped[int | None] = mapped_column(SMALLINT(unsigned=True), nullable=True)
    process_snapshot: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    last_updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    is_active: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE, nullable=False, server_default=TIMESTAMP_NOW
    )
