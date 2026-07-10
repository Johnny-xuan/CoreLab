"""Declarative ORM base + shared MySQL column helpers.

The helpers exist so every model uses identical column types for ids,
fingerprints, JSON payloads, etc. — keeping the schema uniform and
matching the DDL spec in ``docs/02-data-model.md``.
"""

from __future__ import annotations

from sqlalchemy.dialects.mysql import BIGINT, DATETIME
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql import expression

PK_TYPE = BIGINT(unsigned=True)
FK_TYPE = BIGINT(unsigned=True)
TIMESTAMP_TYPE = DATETIME(fsp=6)
TIMESTAMP_NOW = expression.text("CURRENT_TIMESTAMP(6)")


class Base(DeclarativeBase):
    """Declarative base shared by all CoreLab ORM models.

    ``eager_defaults`` triggers a follow-up SELECT after INSERT so async
    code can read server-generated columns (``created_at``, AUTO_INCREMENT
    ids) without a lazy-load round-trip that fails in async context.
    """

    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012  # SQLAlchemy declarative idiom
