"""Alembic migration env — async-aware.

The standard ``alembic init`` template is sync; CoreLab uses SQLAlchemy
2.0 async + asyncmy, so this env runs migrations through an
``AsyncEngine`` and drives it from a sync wrapper Alembic understands.

Phase 1 ships zero migrations — ``alembic upgrade head`` is a no-op.
Phase 2 adds the first revision (lab / user / setup_token / ssh_public_key).
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from corelab_backend.config import get_settings
from corelab_backend.models import Base
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Alembic Config object provides access to .ini values.
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject DB URL from app settings — prefer the dedicated migration URL
# (full grants) over the runtime URL (restricted, can't run DDL). Phase 2
# invariant #8: runtime user has no DELETE; migrations must run as the
# admin user that has CREATE / ALTER / DROP / REFERENCES.
_settings = get_settings()
config.set_main_option("sqlalchemy.url", _settings.migration_database_url or _settings.database_url)

# Target metadata for autogenerate — Phase 2 wires all identity tables.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — generate SQL without DB connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations through an async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
