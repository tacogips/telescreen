"""Real owner-role migrations preserve schema ownership and append-only policy."""

import asyncio

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import ProgrammingError

from telescreen.db.adopt import adopt_owner
from telescreen.db.migrate import owner_config, upgrade, upgrade_store
from telescreen.db.ownership import Store, main_database_tables, tables_for
from tests.support.owned_postgres import OwnedPostgres, owned_postgres

__all__: list[str] = ["owned_postgres"]
pytestmark: list[pytest.MarkDecorator] = [pytest.mark.asyncio, pytest.mark.integration]


async def test_full_owner_migrations_use_only_their_own_credentials(
    owned_postgres: OwnedPostgres,
) -> None:
    """All three complete histories repeat under restricted roles and cannot cross schemas."""
    for url, store in (
        (owned_postgres.main_url, Store.MAIN),
        (owned_postgres.inventory_url, Store.INVENTORY),
        (owned_postgres.notification_url, Store.NOTIFICATION),
    ):
        await asyncio.to_thread(upgrade_store, url, store)
    for engine, store, schema, forbidden_schema, forbidden_table, protected, expected in (
        (
            owned_postgres.main,
            Store.MAIN,
            owned_postgres.main_schema,
            owned_postgres.inventory_schema,
            "data_assets",
            "audit_records",
            main_database_tables(),
        ),
        (
            owned_postgres.inventory,
            Store.INVENTORY,
            owned_postgres.inventory_schema,
            owned_postgres.main_schema,
            "members",
            "data_asset_events",
            tables_for(Store.INVENTORY),
        ),
        (
            owned_postgres.notification,
            Store.NOTIFICATION,
            owned_postgres.notification_schema,
            owned_postgres.main_schema,
            "members",
            "notification_audit_records",
            tables_for(Store.NOTIFICATION),
        ),
    ):
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT current_user")) == schema
            names: set[str] = await connection.run_sync(
                lambda sync: set(inspect(sync).get_table_names())
            )
            assert names == {*expected, f"alembic_version_{store}"}
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_trigger WHERE tgrelid = CAST(:table AS regclass) "
                        "AND tgname = :trigger AND NOT tgisinternal"
                    ),
                    {"table": protected, "trigger": f"{protected}_append_only"},
                )
                == 1
            )
        async with engine.connect() as connection:
            with pytest.raises(ProgrammingError, match="permission denied"):
                await connection.execute(
                    text(f'SELECT * FROM "{forbidden_schema}".{forbidden_table}')
                )


async def test_legacy_history_and_checked_adoption_under_postgres_owner(
    owned_postgres: OwnedPostgres,
) -> None:
    """A genuine fresh legacy history adopts both owners without mutating table definitions."""
    await asyncio.to_thread(
        command.downgrade, owner_config(owned_postgres.main_url, Store.MAIN), "base"
    )
    await asyncio.to_thread(upgrade, owned_postgres.main_url)
    for store in Store:
        await asyncio.to_thread(adopt_owner, owned_postgres.main_url, store, writers_stopped=True)
    async with owned_postgres.main.connect() as connection:
        assert await connection.scalar(text("SELECT version_num FROM alembic_version_main")) == (
            "main_0001"
        )
        assert await connection.scalar(
            text("SELECT version_num FROM alembic_version_inventory")
        ) == ("inventory_0001")
        assert await connection.scalar(
            text("SELECT version_num FROM alembic_version_notification")
        ) == ("notification_0001")


@pytest.mark.parametrize(
    "mutation",
    [
        "ALTER TABLE data_assets ALTER COLUMN name TYPE VARCHAR(601)",
        "ALTER TABLE data_fields ALTER COLUMN required DROP DEFAULT",
        "ALTER TABLE data_fields ALTER COLUMN required DROP NOT NULL",
        "ALTER TABLE data_asset_events DISABLE TRIGGER data_asset_events_append_only",
        "CREATE OR REPLACE FUNCTION telescreen_reject_data_asset_event_mutation() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END; $$",
    ],
)
async def test_adoption_rejects_postgres_schema_and_trigger_policy_drift(
    owned_postgres: OwnedPostgres, mutation: str
) -> None:
    """Matching table names alone cannot authorize a stamp over damaged schema protection."""
    async with owned_postgres.inventory.begin() as connection:
        await connection.execute(text(mutation))
    with pytest.raises(RuntimeError, match="owner schema|owner append-only"):
        await asyncio.to_thread(
            adopt_owner, owned_postgres.inventory_url, Store.INVENTORY, writers_stopped=True
        )


async def test_adoption_rejects_a_serial_default_rewired_to_another_sequence(
    owned_postgres: OwnedPostgres,
) -> None:
    """An unrelated valid nextval expression is not the baseline column's serial default."""
    async with owned_postgres.main.begin() as connection:
        await connection.execute(text("CREATE SEQUENCE unrelated_sequence"))
        await connection.execute(
            text(
                "ALTER TABLE audit_chain_head ALTER COLUMN id "
                "SET DEFAULT nextval('unrelated_sequence'::regclass)"
            )
        )
    with pytest.raises(RuntimeError, match="default differs"):
        await asyncio.to_thread(
            adopt_owner, owned_postgres.main_url, Store.MAIN, writers_stopped=True
        )
