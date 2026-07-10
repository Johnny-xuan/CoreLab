"""Async SQLAlchemy engine + session factory.

The engine is created at first use (lazy) so importing this module in
tests doesn't try to open a real connection. ``ping_db`` is a lightweight
``SELECT 1`` used by ``/readyz`` to confirm MySQL reachability.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Return the singleton async engine."""
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=5,
        echo=False,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return a session factory bound to the singleton engine."""
    return async_sessionmaker(
        bind=get_engine(),
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yield a session, commit/rollback on exit.

    HTTPException is treated as an expected control-flow signal (4xx —
    bad credentials, conflict, etc.); the session is committed so any
    audit_log row written before the raise persists. Other exceptions
    are unexpected (5xx) and rollback.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except HTTPException:
            await session.commit()
            raise
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


async def ping_db() -> bool:
    """Run a trivial query to confirm the engine can talk to MySQL."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        return result.scalar() == 1
