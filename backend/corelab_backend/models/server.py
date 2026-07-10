"""Server — a GPU host in the lab that runs a corelab-agent.

Lifecycle: ``pending`` (waiting for agent phone-home and lab-admin
approval) → ``online`` (approved + heartbeat fresh) → ``offline`` (no
heartbeat in 5 min, scheduler flips it) → ``maintenance`` (admin manual).
Soft-delete via ``is_active=0`` only — id is never reused so FK history
(reservations, audit_log) stays intact.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import CHAR, INTEGER, TINYINT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import FK_TYPE, PK_TYPE, TIMESTAMP_NOW, TIMESTAMP_TYPE, Base


class Server(Base):
    __tablename__ = "server"
    __table_args__ = (
        UniqueConstraint("lab_id", "hostname", name="uq_server_lab_hostname"),
        Index("idx_server_status", "status", "last_heartbeat_at"),
        CheckConstraint(
            "status IN ('pending', 'online', 'offline', 'maintenance')",
            name="ck_server_status",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    lab_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey("lab.id", name="fk_server_lab", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    hostname: Mapped[str] = mapped_column(VARCHAR(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(VARCHAR(100), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(VARCHAR(45), nullable=True)
    os_info: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    kernel_version: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    cpu_model: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    cpu_cores: Mapped[int | None] = mapped_column(INTEGER(unsigned=True), nullable=True)
    memory_total_mb: Mapped[int | None] = mapped_column(INTEGER(unsigned=True), nullable=True)
    agent_version: Mapped[str | None] = mapped_column(VARCHAR(32), nullable=True)
    agent_token_hash: Mapped[str | None] = mapped_column(CHAR(60), nullable=True)
    status: Mapped[str] = mapped_column(VARCHAR(32), nullable=False, server_default="pending")
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    max_reservation_hours: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=True), nullable=True, server_default="24"
    )
    is_active: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE, nullable=False, server_default=TIMESTAMP_NOW
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        FK_TYPE,
        ForeignKey("user.id", name="fk_server_creator", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    approved_by_user_id: Mapped[int | None] = mapped_column(
        FK_TYPE,
        ForeignKey("user.id", name="fk_server_approver", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
