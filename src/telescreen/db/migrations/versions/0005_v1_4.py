"""Add the v1.4 data inventory schema."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import MetaData, Table, text
from sqlalchemy.engine import Connection

from telescreen.db import models as _models

revision: str = "0005_v1_4"
down_revision: str | None = "0004_v1_3"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

NEW_TABLES: tuple[str, ...] = (
    "data_assets",
    "data_asset_events",
    "data_asset_items",
    "data_fields",
    "data_field_values",
)
NEW_COLUMNS: tuple[tuple[str, tuple[str, ...]], ...] = (("collector_configs", ("inventory_mode",)),)


def upgrade() -> None:
    """Create missing inventory schema and seed its asset counter."""
    bind: Connection = op.get_bind()
    inspector: sa.Inspector = sa.inspect(bind)
    existing_tables: set[str] = set(inspector.get_table_names())
    for table_name in NEW_TABLES:
        if table_name not in existing_tables:
            table: Table = _models.DataAssetRow.metadata.tables[table_name]
            cloned: Table = table.to_metadata(MetaData())
            op.create_table(table.name, *list(cloned.columns), *list(cloned.constraints))
            existing_tables.add(table_name)

    for table_name, column_names in NEW_COLUMNS:
        existing_columns: set[str] = {
            column["name"] for column in sa.inspect(bind).get_columns(table_name)
        }
        for column_name in column_names:
            if column_name not in existing_columns:
                column: sa.Column[str] = _models.DataAssetRow.metadata.tables[table_name].c[
                    column_name
                ]
                op.add_column(table_name, column._copy())
                existing_columns.add(column_name)

    for table_name in NEW_TABLES:
        table = _models.DataAssetRow.metadata.tables[table_name]
        existing_indexes: set[str] = {
            str(index["name"])
            for index in sa.inspect(bind).get_indexes(table_name)
            if index["name"] is not None
        }
        for index in table.indexes:
            if index.name is None or index.name in existing_indexes:
                continue
            index_columns: list[str] = [column.name for column in index.columns]
            op.create_index(index.name, table_name, index_columns, unique=index.unique)
            existing_indexes.add(index.name)

    bind.execute(
        text(
            "INSERT INTO counters (name, value) "
            "SELECT 'data_asset_number', 0 "
            "WHERE NOT EXISTS (SELECT 1 FROM counters WHERE name = 'data_asset_number')"
        )
    )
    if bind.dialect.name == "postgresql":
        op.execute(
            text(
                "CREATE OR REPLACE FUNCTION telescreen_reject_data_asset_event_mutation() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                "RAISE EXCEPTION 'data_asset_events is append-only'; END; $$"
            )
        )
        op.execute(
            text(
                "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_trigger "
                "WHERE tgrelid = 'data_asset_events'::regclass "
                "AND tgname = 'data_asset_events_append_only' AND NOT tgisinternal) THEN "
                "EXECUTE 'CREATE TRIGGER data_asset_events_append_only BEFORE UPDATE OR DELETE "
                "ON data_asset_events FOR EACH ROW EXECUTE FUNCTION "
                "telescreen_reject_data_asset_event_mutation()'; END IF; END $$"
            )
        )


def downgrade() -> None:
    """Drop the v1.4 inventory trigger, tables and collector column."""
    bind: Connection = op.get_bind()
    existing_tables: set[str] = set(sa.inspect(bind).get_table_names())
    if bind.dialect.name == "postgresql" and "data_asset_events" in existing_tables:
        op.execute(
            text("DROP TRIGGER IF EXISTS data_asset_events_append_only ON data_asset_events")
        )
        op.execute(text("DROP FUNCTION IF EXISTS telescreen_reject_data_asset_event_mutation()"))

    for table_name in reversed(NEW_TABLES):
        if table_name in existing_tables:
            op.drop_table(table_name)

    for table_name, column_names in NEW_COLUMNS:
        if table_name not in existing_tables:
            continue
        existing_columns: set[str] = {
            column["name"] for column in sa.inspect(bind).get_columns(table_name)
        }
        columns_to_drop: tuple[str, ...] = tuple(
            column_name for column_name in column_names if column_name in existing_columns
        )
        if not columns_to_drop:
            continue
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(table_name) as batch:
                for column_name in columns_to_drop:
                    batch.drop_column(column_name)
        else:
            for column_name in columns_to_drop:
                op.drop_column(table_name, column_name)
