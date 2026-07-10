"""Lab — the top-level tenant container.

CoreLab v1 is single-tenant: there will only ever be one row (id=1).
The ``lab_id`` foreign key is still threaded through downstream tables
to keep the future migration path to multi-tenant cheap (NFR-X3).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Text, UniqueConstraint, text
from sqlalchemy.dialects.mysql import JSON, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from .base import PK_TYPE, TIMESTAMP_NOW, TIMESTAMP_TYPE, Base


class Lab(Base):
    __tablename__ = "lab"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_lab_slug"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(VARCHAR(100), nullable=False)
    slug: Mapped[str] = mapped_column(VARCHAR(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Phase M v5 — multi-URL public access. Each entry:
    #   {url, kind, source, verified_at, last_reachable_at, primary}
    # See alembic migration 20260609_*_phase_m_lab_public_urls.py for the
    # shape. Mutated only through lab_url_service to keep schema in sync.
    public_urls: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, server_default=text("(JSON_ARRAY())")
    )
    tunnel_mode: Mapped[str] = mapped_column(
        VARCHAR(32), nullable=False, server_default=text("'none'")
    )
    tunnel_token: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE, nullable=False, server_default=TIMESTAMP_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE,
        nullable=False,
        server_default=TIMESTAMP_NOW,
        server_onupdate=TIMESTAMP_NOW,
    )
