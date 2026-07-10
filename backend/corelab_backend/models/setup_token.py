"""SetupToken — one-time token for account activation / password reset.

Only the SHA-256 hash of the token is stored; the plaintext is delivered
once (URL) at issue time and never persisted.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import CHAR, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import FK_TYPE, PK_TYPE, TIMESTAMP_NOW, TIMESTAMP_TYPE, Base


class SetupToken(Base):
    __tablename__ = "setup_token"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_setup_token_hash"),
        Index("idx_setup_token_user", "user_id", "used_at"),
        CheckConstraint(
            "purpose IN ('activation', 'password_reset')",
            name="ck_setup_token_purpose",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey("user.id", name="fk_setup_token_user", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    purpose: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP_TYPE, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE, nullable=False, server_default=TIMESTAMP_NOW
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        FK_TYPE,
        ForeignKey(
            "user.id", name="fk_setup_token_creator", ondelete="SET NULL", onupdate="CASCADE"
        ),
        nullable=True,
    )
