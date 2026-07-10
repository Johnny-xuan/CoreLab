"""ServerAdminGrant — lab_admin delegates per-server admin to a user.

Active grants are unique per (user, server) via the ``is_active_uk``
generated column NULL trick (MySQL partial UNIQUE workaround). Revoked
rows stay for audit history.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Computed, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import TINYINT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import FK_TYPE, PK_TYPE, TIMESTAMP_NOW, TIMESTAMP_TYPE, Base


class ServerAdminGrant(Base):
    __tablename__ = "server_admin_grant"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "server_id", "is_active_uk", name="uq_grant_user_server_active"
        ),
        Index("idx_grant_server", "server_id", "is_active"),
        Index("idx_grant_user", "user_id", "is_active"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey("user.id", name="fk_grant_user", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    server_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey("server.id", name="fk_grant_server", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    granted_by_user_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey("user.id", name="fk_grant_granter", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    granted_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE, nullable=False, server_default=TIMESTAMP_NOW
    )
    notes: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    is_active: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default="1")
    is_active_uk: Mapped[int | None] = mapped_column(
        TINYINT(1), Computed("IF(is_active=1,1,NULL)", persisted=True), nullable=True
    )
    revoked_by_user_id: Mapped[int | None] = mapped_column(
        FK_TYPE,
        ForeignKey("user.id", name="fk_grant_revoker", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
