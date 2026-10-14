"""Non-owner runtime credentials cannot use retained inventory tables after cutover."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from telescreen.core.ids import new_id
from telescreen.db.migrate import upgrade_store
from telescreen.db.ownership import Store, tables_for
from tests.e2e.harness import E2EWorld, OutboundRecorder, make_world
from tests.e2e.inventory_split_flow import assert_split_runtime
from tests.support.owned_postgres import OwnedPostgres, owned_postgres

__all__: list[str] = ["owned_postgres"]
pytestmark: list[pytest.MarkDecorator] = [pytest.mark.asyncio, pytest.mark.integration]


@dataclass(frozen=True)
class RuntimeGrants:
    """Runtime logins with DML grants, no ownership and no migration-state privileges."""

    main: AsyncEngine
    inventory: AsyncEngine
    main_url: str = field(repr=False)
    inventory_url: str = field(repr=False)


@pytest_asyncio.fixture
async def runtime_grants(owned_postgres: OwnedPostgres) -> AsyncIterator[RuntimeGrants]:
    """Retain all ten old tables in main but grant its runtime only the 52 main tables."""
    await asyncio.to_thread(upgrade_store, owned_postgres.main_url, Store.INVENTORY)
    suffix: str = new_id().replace("-", "")[:16]
    password: str = new_id().replace("-", "")
    main_role: str = f"ts_run_main_{suffix}"
    inventory_role: str = f"ts_run_inv_{suffix}"
    main_url: str = (
        make_url(owned_postgres.main_url)
        .set(username=main_role, password=password)
        .render_as_string(hide_password=False)
    )
    inventory_url: str = (
        make_url(owned_postgres.inventory_url)
        .set(username=inventory_role, password=password)
        .render_as_string(hide_password=False)
    )
    main: AsyncEngine = create_async_engine(main_url)
    inventory: AsyncEngine = create_async_engine(inventory_url)
    try:
        async with owned_postgres.admin.begin() as connection:
            for role, schema, store in (
                (main_role, owned_postgres.main_schema, Store.MAIN),
                (inventory_role, owned_postgres.inventory_schema, Store.INVENTORY),
            ):
                await connection.execute(
                    text(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}'")
                )
                await connection.execute(text(f'ALTER ROLE "{role}" SET search_path = "{schema}"'))
                await connection.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"'))
                tables: str = ", ".join(f'"{schema}"."{name}"' for name in tables_for(store))
                await connection.execute(
                    text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {tables} TO "{role}"')
                )
                await connection.execute(
                    text(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "{schema}" TO "{role}"')
                )
        yield RuntimeGrants(main, inventory, main_url, inventory_url)
    finally:
        await main.dispose()
        await inventory.dispose()
        async with owned_postgres.admin.begin() as connection:
            for role in (main_role, inventory_role):
                await connection.execute(text(f'DROP OWNED BY "{role}"'))
                await connection.execute(text(f'DROP ROLE "{role}"'))


async def test_full_runtime_with_nonowner_roles_and_retained_inventory_backup_tables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owned_postgres: OwnedPostgres,
    runtime_grants: RuntimeGrants,
) -> None:
    """API/worker need only owner-local DML, despite old inventory tables existing in main."""
    for engine, table, forbidden in (
        (runtime_grants.main, "data_assets", owned_postgres.inventory_schema),
        (runtime_grants.inventory, "members", owned_postgres.main_schema),
    ):
        async with engine.connect() as connection:
            assert await connection.scalar(
                text(
                    "SELECT NOT rolsuper AND NOT rolcreaterole FROM pg_roles "
                    "WHERE rolname = current_user"
                )
            )
            with pytest.raises(ProgrammingError, match="permission denied"):
                await connection.execute(text(f'SELECT * FROM "{forbidden}".{table}'))
    async with runtime_grants.main.connect() as connection:
        with pytest.raises(ProgrammingError, match="permission denied"):
            await connection.execute(text("SELECT * FROM data_assets"))
    async with runtime_grants.inventory.connect() as connection:
        with pytest.raises(ProgrammingError, match="must be owner"):
            await connection.execute(
                text("ALTER TABLE data_asset_events DISABLE TRIGGER data_asset_events_append_only")
            )
    for engine, table in (
        (runtime_grants.main, "alembic_version_main"),
        (runtime_grants.inventory, "alembic_version_inventory"),
    ):
        async with engine.connect() as connection:
            with pytest.raises(ProgrammingError, match="permission denied"):
                await connection.execute(text(f"SELECT * FROM {table}"))
    world: E2EWorld = await make_world(
        tmp_path,
        (("TELESCREEN_DATA_OVERDUE_CRON", "* * * * *"),),
        OutboundRecorder(),
        store_urls=(runtime_grants.main_url, runtime_grants.inventory_url),
        migrate_stores=False,
    )
    await assert_split_runtime(world, monkeypatch)
