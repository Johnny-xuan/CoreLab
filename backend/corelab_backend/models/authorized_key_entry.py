"""AuthorizedKeyEntry — tracks which SSH public keys CoreLab has pushed.

Records only keys we put there ourselves; keys the user dropped in
``~/.ssh/authorized_keys`` by hand are invisible to us and stay
untouched. Used for revoke-on-cascade (delete only the key we pushed
when a link is revoked) and for auditing who-pushed-whose-key.

Soft-delete via ``is_active=0``; the row stays so revoke history is
retrievable. Active-uniqueness on ``(pa, key)`` is enforced via the
regular UNIQUE — same key re-pushed after a revoke needs the prior row
flipped to ``is_active=0`` first (or use a fresh ``ssh_public_key`` id).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from .base import FK_TYPE, PK_TYPE, TIMESTAMP_NOW, TIMESTAMP_TYPE, Base


class AuthorizedKeyEntry(Base):
    __tablename__ = "authorized_key_entry"
    __table_args__ = (
        UniqueConstraint("physical_account_id", "ssh_public_key_id", name="uq_ake_pa_key"),
        Index("idx_ake_pa_active", "physical_account_id", "is_active"),
        Index("idx_ake_user", "pushed_for_user_id"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    physical_account_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey("physical_account.id", name="fk_ake_pa", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    ssh_public_key_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey("ssh_public_key.id", name="fk_ake_key", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    pushed_by_user_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey("user.id", name="fk_ake_pusher", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    pushed_for_user_id: Mapped[int] = mapped_column(
        FK_TYPE,
        ForeignKey("user.id", name="fk_ake_for_user", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    pushed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE, nullable=False, server_default=TIMESTAMP_NOW
    )
    is_active: Mapped[int] = mapped_column(TINYINT(1), nullable=False, server_default="1")
    removed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE, nullable=True)
    removed_by_user_id: Mapped[int | None] = mapped_column(
        FK_TYPE,
        ForeignKey("user.id", name="fk_ake_remover", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
