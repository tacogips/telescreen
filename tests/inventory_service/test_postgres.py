"""Restricted PostgreSQL roles for the three service stores."""

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from telescreen.core.ids import new_id
from telescreen.db.ownership import Store, main_database_tables, tables_for
from tests.support.owned_postgres import OwnedPostgres


@pytest.mark.asyncio
@pytest.mark.integration
async def test_three_owner_roles_cannot_read_other_stores_or_migration_state(
    owned_postgres: OwnedPostgres,
) -> None:
    """Single-process Main and full-split roles receive owner-local table grants only."""
    suffix: str = new_id().replace("-", "")[:12]
    password: str = new_id().replace("-", "")
    roles: tuple[tuple[str, str, tuple[str, ...]], ...] = (
        (f"ts_main_{suffix}", owned_postgres.main_schema, main_database_tables()),
        (f"ts_inv_{suffix}", owned_postgres.inventory_schema, tables_for(Store.INVENTORY)),
        (
            f"ts_notification_{suffix}",
            owned_postgres.notification_schema,
            tables_for(Store.NOTIFICATION),
        ),
    )
    engines: list[AsyncEngine] = []
    try:
        async with owned_postgres.admin.begin() as connection:
            for role, schema, tables in roles:
                await connection.execute(
                    text(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}'")
                )
                await connection.execute(text(f'ALTER ROLE "{role}" SET search_path = "{schema}"'))
                await connection.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"'))
                relation_list: str = ", ".join(f'"{schema}"."{table}"' for table in tables)
                await connection.execute(
                    text(
                        f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {relation_list} TO "{role}"'
                    )
                )
                await connection.execute(
                    text(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "{schema}" TO "{role}"')
                )
        for role, schema, tables in roles:
            engine: AsyncEngine = create_async_engine(
                make_url(owned_postgres.main_url)
                .set(username=role, password=password)
                .render_as_string(hide_password=False)
            )
            engines.append(engine)
            async with engine.connect() as connection:
                assert await connection.scalar(
                    text(
                        "SELECT NOT rolsuper AND NOT rolcreaterole "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                )
                await connection.execute(text(f'SELECT * FROM "{schema}"."{tables[0]}" LIMIT 0'))
                with pytest.raises(ProgrammingError, match="permission denied"):
                    await connection.execute(
                        text(f'SELECT * FROM "{schema}".alembic_version_{schema.split("_")[1]}')
                    )
                other_schema: str = next(
                    candidate[1] for candidate in roles if candidate[1] != schema
                )
                other_table: str = next(
                    candidate_table
                    for candidate in roles
                    if candidate[1] == other_schema
                    for candidate_table in candidate[2]
                )
                with pytest.raises(ProgrammingError, match="permission denied"):
                    await connection.execute(
                        text(f'SELECT * FROM "{other_schema}"."{other_table}"')
                    )
                with pytest.raises(ProgrammingError, match="permission denied"):
                    await connection.execute(
                        text(f'INSERT INTO "{other_schema}"."{other_table}" DEFAULT VALUES')
                    )
        inventory_engine: AsyncEngine = engines[1]
        async with inventory_engine.connect() as connection:
            with pytest.raises(ProgrammingError, match="must be owner"):
                await connection.execute(
                    text(
                        f'ALTER TABLE "{owned_postgres.inventory_schema}".data_asset_events '
                        "DISABLE TRIGGER data_asset_events_append_only"
                    )
                )
    finally:
        for engine in engines:
            await engine.dispose()
        async with owned_postgres.admin.begin() as connection:
            for role, _, _ in roles:
                await connection.execute(text(f'DROP OWNED BY "{role}"'))
                await connection.execute(text(f'DROP ROLE "{role}"'))
