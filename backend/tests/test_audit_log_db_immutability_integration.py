"""Live MySQL proof for the audit_log database immutability invariant."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.asyncio


def _db_urls() -> tuple[str, str] | None:
    runtime_url = os.environ.get("CORELAB_DATABASE_URL", "")
    migration_url = os.environ.get("CORELAB_MIGRATION_DATABASE_URL", "")
    if not runtime_url or not migration_url:
        return None
    if "127.0.0.1" not in runtime_url or "127.0.0.1" not in migration_url:
        return None
    return runtime_url, migration_url


async def test_runtime_app_user_cannot_update_or_delete_audit_log() -> None:
    urls = _db_urls()
    if urls is None:
        pytest.skip("audit_log immutability proof needs local MySQL runtime and migration URLs")

    runtime_url, migration_url = urls
    runtime_engine = create_async_engine(runtime_url, pool_pre_ping=True)
    migration_engine = create_async_engine(migration_url, pool_pre_ping=True)

    try:
        async with migration_engine.begin() as conn:
            result = await conn.execute(
                text(
                    "INSERT INTO audit_log (action, target_type, result) "
                    "VALUES ('audit.immutability.probe', 'audit_log', 'ok')"
                )
            )
            audit_id = result.lastrowid

        async with runtime_engine.begin() as conn:
            with pytest.raises(SQLAlchemyError) as update_error:
                await conn.execute(
                    text("UPDATE audit_log SET action = 'audit.tampered' WHERE id = :id"),
                    {"id": audit_id},
                )
            assert "append-only" in str(update_error.value)

        async with runtime_engine.begin() as conn:
            with pytest.raises(SQLAlchemyError) as delete_error:
                await conn.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": audit_id})
            delete_message = str(delete_error.value).lower()
            assert "append-only" in delete_message or "delete command denied" in delete_message

        async with migration_engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT action, target_type FROM audit_log WHERE id = :id"),
                    {"id": audit_id},
                )
            ).one()
            assert row.action == "audit.immutability.probe"
            assert row.target_type == "audit_log"
    finally:
        await runtime_engine.dispose()
        await migration_engine.dispose()
