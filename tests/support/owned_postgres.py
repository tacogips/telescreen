"""Full owner stores migrated using separate restricted PostgreSQL login roles."""

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from telescreen.core.ids import new_id
from telescreen.db.migrate import upgrade_store
from telescreen.db.ownership import Store


@dataclass(frozen=True)
class OwnedPostgres:
    """Migrated owner engines and administrator-only permission test access."""

    main: AsyncEngine
    inventory: AsyncEngine
    admin: AsyncEngine
    main_schema: str
    inventory_schema: str
    main_url: str = field(repr=False)
    inventory_url: str = field(repr=False)


@pytest_asyncio.fixture
async def owned_postgres() -> AsyncIterator[OwnedPostgres]:
    """Use production migration entry points with no injected privileged connection."""
    configured: str | None = os.environ.get("TELESCREEN_TEST_DATABASE_URL")
    if configured is None:
        pytest.skip("requires TELESCREEN_TEST_DATABASE_URL with PostgreSQL role creation")
    url: URL = make_url(configured)
    if url.get_backend_name() != "postgresql":
        pytest.skip("requires PostgreSQL")
    admin: AsyncEngine = create_async_engine(url)
    async with admin.connect() as connection:
        capable: bool = bool(
            await connection.scalar(
                text("SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user")
            )
        )
    if not capable:
        await admin.dispose()
        pytest.skip("requires permission to create isolated test roles")
    suffix: str = new_id().replace("-", "")[:16]
    password: str = new_id().replace("-", "")
    main_schema: str = f"ts_main_{suffix}"
    inventory_schema: str = f"ts_inventory_{suffix}"
    main_url: str = url.set(username=main_schema, password=password).render_as_string(
        hide_password=False
    )
    inventory_url: str = url.set(username=inventory_schema, password=password).render_as_string(
        hide_password=False
    )
    main: AsyncEngine = create_async_engine(main_url)
    inventory: AsyncEngine = create_async_engine(inventory_url)
    try:
        async with admin.begin() as connection:
            for schema in (main_schema, inventory_schema):
                await connection.execute(
                    text(f"CREATE ROLE \"{schema}\" LOGIN PASSWORD '{password}'")
                )
                await connection.execute(text(f'CREATE SCHEMA "{schema}" AUTHORIZATION "{schema}"'))
                await connection.execute(
                    text(f'ALTER ROLE "{schema}" SET search_path = "{schema}"')
                )
        await asyncio.to_thread(upgrade_store, main_url, Store.MAIN)
        await asyncio.to_thread(upgrade_store, inventory_url, Store.INVENTORY)
        yield OwnedPostgres(
            main, inventory, admin, main_schema, inventory_schema, main_url, inventory_url
        )
    finally:
        await main.dispose()
        await inventory.dispose()
        async with admin.begin() as connection:
            for schema in (main_schema, inventory_schema):
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                await connection.execute(text(f'DROP ROLE IF EXISTS "{schema}"'))
        await admin.dispose()
