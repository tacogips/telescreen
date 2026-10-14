"""Alembic migration environment without an ini-file dependency."""

import asyncio

from alembic import context
from alembic.config import Config
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, async_engine_from_config
from sqlalchemy.pool import NullPool

config: Config = context.config


def run_migrations_offline() -> None:
    """Render migrations without connecting to a database."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations on an established synchronous connection."""
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migration operations synchronously."""
    connectable: AsyncEngine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}) or {},
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations against the configured async database URL."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
