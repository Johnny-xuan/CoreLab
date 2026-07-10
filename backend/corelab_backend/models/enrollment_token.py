"""EnrollmentToken — one-time secret for an agent to phone home.

lab_admin mints one via POST /servers (the plaintext is returned
once + embedded in the install snippet). agent's first connect
includes the token; backend verifies SHA-256 hash, then binds
``used_by_server_id`` and stamps ``used_at``. Further reconnects use
the persisted ``server.agent_token_hash`` (bcrypt) instead.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import CHAR, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import FK_TYPE, PK_TYPE, TIMESTAMP_NOW, TIMESTAMP_TYPE, Base


class EnrollmentToken(Base):
    __tablename__ = "enrollment_token"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_enroll_token_hash"),
        Index("idx_enroll_token_lab_used", "lab_id", "used_at"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    lab_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey("lab.id", name="fk_enroll_token_lab", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    expected_hostname_pattern: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP_TYPE, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    used_by_server_id: Mapped[int | None] = mapped_column(
        FK_TYPE,
        ForeignKey(
            "server.id", name="fk_enroll_token_server", ondelete="SET NULL", onupdate="CASCADE"
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE, nullable=False, server_default=TIMESTAMP_NOW
    )
    created_by_user_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey(
            "user.id", name="fk_enroll_token_creator", ondelete="RESTRICT", onupdate="CASCADE"
        ),
        nullable=False,
    )
