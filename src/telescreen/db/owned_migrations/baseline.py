"""Frozen baseline execution shared by the two independent migration histories."""

import re
from pathlib import Path

from alembic import op
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection


def _snapshot(name: str, dialect: str) -> str:
    """Load packaged SQL; future model changes cannot alter historical revisions."""
    if name not in ("main_0001", "inventory_0001") or dialect not in ("sqlite", "postgresql"):
        raise RuntimeError("unsupported owner migration snapshot")
    return Path(__file__).with_name(f"{name}.{dialect}.sql").read_text(encoding="utf-8")


def snapshot_tables(name: str, dialect: str) -> tuple[str, ...]:
    """Read the immutable table set rather than the current ownership declaration."""
    return tuple(re.findall(r"CREATE TABLE (\w+) ", _snapshot(name, dialect)))


def create_baseline(name: str) -> None:
    """Initialize an empty owner store; never silently adopt an existing deployment."""
    connection: Connection = op.get_bind()
    dialect: str = connection.dialect.name
    tables: tuple[str, ...] = snapshot_tables(name, dialect)
    existing: set[str] = set(inspect(connection).get_table_names())
    if existing.intersection(tables):
        raise RuntimeError("owner baseline requires an empty store; existing schema needs adoption")
    sql: str = _snapshot(name, dialect)
    for statement in sql.split(";"):
        if statement.strip():
            connection.execute(text(statement))


def drop_baseline(name: str) -> None:
    """Drop only the tables defined by this historical owner baseline."""
    connection: Connection = op.get_bind()
    for table_name in reversed(snapshot_tables(name, connection.dialect.name)):
        op.drop_table(table_name)


def append_only(table: str, *, drop: bool = False) -> None:
    """Preserve the append-only policy independently in each owner's database."""
    if table not in ("audit_records", "data_asset_events"):
        raise RuntimeError("unsupported append-only table")
    connection: Connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    function: str = (
        "telescreen_reject_audit_mutation"
        if table == "audit_records"
        else "telescreen_reject_data_asset_event_mutation"
    )
    if drop:
        connection.execute(text(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}"))
        connection.execute(text(f"DROP FUNCTION IF EXISTS {function}()"))
        return
    connection.execute(
        text(
            f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            f"RAISE EXCEPTION '{table} is append-only'; END; $$"
        )
    )
    connection.execute(
        text(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {function}()"
        )
    )
