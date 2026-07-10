"""User — a platform user belonging to a single lab.

``role`` is platform-level and limited to ``user`` or ``lab_admin``;
per-server admin delegation lives in ``server_admin_grant`` (Phase 3+).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import CHAR, TINYINT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import FK_TYPE, PK_TYPE, TIMESTAMP_NOW, TIMESTAMP_TYPE, Base


class User(Base):
    __tablename__ = "user"
    __table_args__ = (
        UniqueConstraint("lab_id", "username", name="uq_user_lab_username"),
        UniqueConstraint("lab_id", "email", name="uq_user_lab_email"),
        Index("idx_user_is_active", "is_active"),
        CheckConstraint("role IN ('user', 'lab_admin')", name="ck_user_role"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    lab_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey("lab.id", name="fk_user_lab", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    username: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    email: Mapped[str] = mapped_column(VARCHAR(255), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(CHAR(60), nullable=True)
    display_name: Mapped[str] = mapped_column(VARCHAR(100), nullable=False)
    role: Mapped[str] = mapped_column(VARCHAR(32), nullable=False, server_default="user")
    is_active: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default="1")
    last_login_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE, nullable=False, server_default=TIMESTAMP_NOW
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        FK_TYPE,
        ForeignKey("user.id", name="fk_user_creator", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE,
        nullable=False,
        server_default=TIMESTAMP_NOW,
        server_onupdate=TIMESTAMP_NOW,
    )

    @property
    def is_activated(self) -> bool:
        """True once the user has set a password via the activation flow.

        Separate axis from ``is_active`` (admin enable/disable): an invited
        account is ``is_active=1`` but ``password_hash IS NULL`` until the
        user completes activation. Lets the UI tell "pending" from "active".
        """
        return self.password_hash is not None
