"""PhysicalAccount — a Linux user account on one server.

One row per ``/etc/passwd`` entry that CoreLab knows about. Provenance
recorded in ``source`` — agent_created (we did ``useradd``), discovered_scan
(agent saw it during a scan), or admin_manual_register (admin typed it in
without anyone touching the server). The ``is_active_uk`` generated-column
NULL trick lets us keep history while enforcing ``(server, linux_username)``
uniqueness among live rows.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, Computed, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import INTEGER, TINYINT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import FK_TYPE, PK_TYPE, TIMESTAMP_NOW, TIMESTAMP_TYPE, Base


class PhysicalAccount(Base):
    __tablename__ = "physical_account"
    __table_args__ = (
        UniqueConstraint(
            "server_id", "linux_username", "is_active_uk", name="uq_pa_server_user_active"
        ),
        Index("idx_pa_server", "server_id", "is_active"),
        CheckConstraint(
            "source IN ('agent_created','discovered_scan','admin_manual_register')",
            name="ck_pa_source",
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
        ForeignKey("server.id", name="fk_pa_server", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    linux_username: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    uid: Mapped[int | None] = mapped_column(INTEGER(unsigned=True), nullable=True)
    gid: Mapped[int | None] = mapped_column(INTEGER(unsigned=True), nullable=True)
    home_directory: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    default_shell: Mapped[str | None] = mapped_column(VARCHAR(100), nullable=True)
    source: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    is_active: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default="1")
    is_active_uk: Mapped[int | None] = mapped_column(
        TINYINT(1), Computed("IF(is_active=1,1,NULL)", persisted=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE, nullable=False, server_default=TIMESTAMP_NOW
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        FK_TYPE,
        ForeignKey("user.id", name="fk_pa_creator", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    # Stamped by every agent account scan that includes this username;
    # NULL = never seen by a scan. A stale value on an online server is
    # the "userdel happened behind our back" signal — surfaced in the
    # UI, never auto-deleted.
    last_seen_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
