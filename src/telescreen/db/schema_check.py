"""Validate existing owner tables against the immutable baseline before adoption."""

import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    Index,
    Inspector,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    UniqueConstraint,
    inspect,
    text,
)
from sqlalchemy.engine import Connection
from sqlalchemy.schema import CreateIndex, DefaultClause

from telescreen.db.migrations.frozen import read_metadata
from telescreen.db.owned_migrations.baseline import snapshot_tables
from telescreen.db.ownership import Store


def baseline_metadata() -> MetaData:
    """Return the baseline schema even after future runtime models have changed."""
    return read_metadata(Path(__file__).parent / "owned_migrations/baseline_schema.json", 1)


def _default(value: str | None) -> str | None:
    """Normalize reflected PostgreSQL casts and literal quoting, keeping values exact."""
    if value is None:
        return None
    normalized: str = re.sub(r"::[a-zA-Z_ ]+(?:\(\d+\))?$", "", value.strip())
    if normalized.startswith("'") and normalized.endswith("'"):
        return normalized[1:-1].replace("''", "'")
    return normalized


def _index_key(index: Index, connection: Connection) -> str:
    """Include predicates, expressions and uniqueness, independent of reflection order."""
    sql: str = str(CreateIndex(index).compile(dialect=connection.dialect))
    return sql.replace(" USING btree", "")


def _type_key(column: Column[str | int | float | bool | datetime], connection: Connection) -> str:
    """Reject width, precision, collation and timezone drift across dialect type classes."""
    if isinstance(column.type, Float):
        return f"float:{column.type.precision or 53}"
    return column.type.compile(dialect=connection.dialect).upper()


def _server_default(column: Column[str | int | float | bool | datetime]) -> str | None:
    """Read literal/default SQL while refusing unsupported generated-value policies."""
    if column.server_default is None:
        return None
    if not isinstance(column.server_default, DefaultClause):
        raise RuntimeError("unsupported owner generated column policy")
    value: str | None = _default(str(column.server_default.arg))
    if isinstance(column.type, Boolean) and value is not None:
        if value.lower() in ("false", "0"):
            return "false"
        if value.lower() in ("true", "1"):
            return "true"
    return value


def _serial_default(connection: Connection, table: str, column: str, value: str | None) -> bool:
    """Accept only the sequence actually owned by this PostgreSQL serial column."""
    reference: re.Match[str] | None = re.fullmatch(
        r"nextval\('((?:[^']|'')+)'::regclass\)", value or ""
    )
    if reference is None:
        return False
    return bool(
        connection.scalar(
            text(
                "SELECT CAST(:sequence AS regclass) = "
                "CAST(pg_get_serial_sequence(:table, :column) AS regclass)"
            ),
            {"sequence": reference.group(1).replace("''", "'"), "table": table, "column": column},
        )
    )


def validate_owner_schema(connection: Connection, store: Store) -> None:
    """Refuse missing, altered or partially upgraded owner tables without writing data."""
    metadata: MetaData = baseline_metadata()
    inspector: Inspector = inspect(connection)
    names: tuple[str, ...] = snapshot_tables(f"{store}_0001", connection.dialect.name)
    if not set(names) <= set(inspector.get_table_names()):
        raise RuntimeError("owner schema is missing baseline tables")
    for name in names:
        expected: Table = metadata.tables[name]
        actual: Table = Table(name, MetaData(), autoload_with=connection)
        if set(actual.c.keys()) != set(expected.c.keys()):
            raise RuntimeError(f"owner schema columns differ: {name}")
        for column in expected.columns:
            found: Column[str | int | float | bool | datetime] = actual.c[column.name]
            if (
                column.type._type_affinity is not found.type._type_affinity
                or column.nullable != found.nullable
                or column.primary_key != found.primary_key
                or _type_key(column, connection) != _type_key(found, connection)
            ):
                raise RuntimeError(f"owner schema column differs: {name}.{column.name}")
            expected_default: str | None = _server_default(column)
            actual_default: str | None = _server_default(found)
            serial: bool = (
                connection.dialect.name == "postgresql"
                and column is expected.autoincrement_column
                and _serial_default(connection, name, column.name, actual_default)
            )
            if not serial and expected_default != actual_default:
                raise RuntimeError(f"owner schema default differs: {name}.{column.name}")
        if tuple(actual.primary_key.columns.keys()) != tuple(expected.primary_key.columns.keys()):
            raise RuntimeError(f"owner schema primary key differs: {name}")
        if {_index_key(index, connection) for index in actual.indexes} != {
            _index_key(index, connection) for index in expected.indexes
        }:
            raise RuntimeError(f"owner schema indexes differ: {name}")
        expected_uniques: set[tuple[str, ...]] = {
            tuple(constraint.columns.keys())
            for constraint in expected.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        actual_uniques: set[tuple[str, ...]] = {
            tuple(constraint.columns.keys())
            for constraint in actual.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        if expected_uniques != actual_uniques or actual.foreign_keys:
            raise RuntimeError(f"owner schema constraints differ: {name}")
        if any(
            not isinstance(constraint, (PrimaryKeyConstraint, UniqueConstraint))
            for constraint in actual.constraints
        ):
            raise RuntimeError(f"owner schema has unsupported constraints: {name}")
        for constraint in expected.constraints:
            if (
                isinstance(constraint, UniqueConstraint)
                and constraint.name is not None
                and not any(
                    isinstance(found, UniqueConstraint)
                    and found.name == constraint.name
                    and tuple(found.columns.keys()) == tuple(constraint.columns.keys())
                    for found in actual.constraints
                )
            ):
                raise RuntimeError(f"owner schema named constraint differs: {name}")
    if connection.dialect.name == "postgresql":
        _validate_trigger(connection, store)


def _validate_trigger(connection: Connection, store: Store) -> None:
    """Require the baseline append-only trigger and its enabled exception function."""
    table: str = "audit_records" if store is Store.MAIN else "data_asset_events"
    function: str = (
        "telescreen_reject_audit_mutation"
        if store is Store.MAIN
        else "telescreen_reject_data_asset_event_mutation"
    )
    valid: int = connection.execute(
        text(
            "SELECT count(*) FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid "
            "WHERE t.tgrelid = CAST(:table AS regclass) AND t.tgname = :trigger "
            "AND NOT t.tgisinternal AND t.tgenabled = 'O' AND t.tgtype = 27 "
            "AND p.proname = :function "
            "AND regexp_replace(trim(p.prosrc), '\\s+', ' ', 'g') = :body"
        ),
        {
            "table": table,
            "trigger": f"{table}_append_only",
            "function": function,
            "body": f"BEGIN RAISE EXCEPTION '{table} is append-only'; END;",
        },
    ).scalar_one()
    if valid != 1:
        raise RuntimeError("owner append-only trigger differs from baseline")
