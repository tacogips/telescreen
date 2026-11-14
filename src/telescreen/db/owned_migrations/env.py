"""Independent owner migrations, without importing application model metadata."""

import asyncio

from alembic import context
from alembic.config import Config
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, async_engine_from_config
from sqlalchemy.pool import NullPool

config: Config = context.config


def migrate(connection: Connection) -> None:
    """Use a separate version table even when owners share one physical database."""
    version_table: str | None = config.get_main_option("owner_version_table")
    if version_table not in (
        "alembic_version_main",
        "alembic_version_inventory",
        "alembic_version_notification",
    ):
        raise RuntimeError("an explicit owner version table is required")
    context.configure(connection=connection, version_table=version_table)
    with context.begin_transaction():
        context.run_migrations()


async def run() -> None:
    """Dispose migration connections even when a revision refuses an unsafe upgrade."""
    engine: AsyncEngine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}) or {},
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    try:
        async with engine.begin() as connection:
            if connection.dialect.name == "sqlite":
                # SQLite's legacy driver mode does not start a transaction for DDL.
                # Include tables, seeds and the version stamp in the same rollback.
                await connection.exec_driver_sql("BEGIN")
            await connection.run_sync(migrate)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("owner migrations require an online database connection")
if isinstance(config.attributes.get("connection"), Connection):
    migrate(config.attributes["connection"])
else:
    asyncio.run(run())
