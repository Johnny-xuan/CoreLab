"""RegistrationInvite -- role-scoped invite token before a user exists."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import CHAR, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import FK_TYPE, PK_TYPE, TIMESTAMP_NOW, TIMESTAMP_TYPE, Base


class RegistrationInvite(Base):
    __tablename__ = "registration_invite"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_registration_invite_token_hash"),
        Index("idx_registration_invite_lab", "lab_id", "used_at"),
        CheckConstraint("role IN ('user', 'lab_admin')", name="ck_registration_invite_role"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    lab_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey(
            "lab.id", name="fk_registration_invite_lab", ondelete="RESTRICT", onupdate="CASCADE"
        ),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    role: Mapped[str] = mapped_column(VARCHAR(32), nullable=False, server_default="user")
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP_TYPE, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    used_by_user_id: Mapped[int | None] = mapped_column(
        FK_TYPE,
        ForeignKey(
            "user.id",
            name="fk_registration_invite_used_by",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE, nullable=False, server_default=TIMESTAMP_NOW
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        FK_TYPE,
        ForeignKey(
            "user.id",
            name="fk_registration_invite_creator",
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        nullable=True,
    )
