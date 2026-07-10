"""SshPublicKey — public keys uploaded by a user.

Soft-deleted (``is_active=0``) rather than hard-deleted so that
authorized-key push history (Phase 4+) stays auditable.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import CHAR, TINYINT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import FK_TYPE, PK_TYPE, TIMESTAMP_NOW, TIMESTAMP_TYPE, Base


class SshPublicKey(Base):
    __tablename__ = "ssh_public_key"
    __table_args__ = (
        UniqueConstraint("user_id", "fingerprint_sha256", name="uq_ssh_key_user_fp"),
        Index("idx_ssh_key_fp", "fingerprint_sha256"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey("user.id", name="fk_ssh_key_user", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint_sha256: Mapped[str] = mapped_column(CHAR(50), nullable=False)
    key_type: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    comment: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    is_active: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE, nullable=False, server_default=TIMESTAMP_NOW
    )
    disabled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
