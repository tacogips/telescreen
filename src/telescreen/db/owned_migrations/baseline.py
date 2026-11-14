"""Frozen baseline execution shared by the independent migration histories."""

import re
from pathlib import Path

from alembic import context, op
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

COLOCATED_NOTIFICATION_TABLES: tuple[str, ...] = (
    "notification_inbox",
    "notification_delivery_state",
    "notification_directory_entries",
    "notification_audit_records",
    "notification_audit_chain_head",
)
RETAINED_NOTIFICATION_TABLES: tuple[str, ...] = (
    "notifications",
    "notification_templates",
    "notification_settings",
    "retention_notification_receipts",
)


def _snapshot(name: str, dialect: str) -> str:
    """Load packaged SQL; future model changes cannot alter historical revisions."""
    if name not in ("main_0001", "inventory_0001", "notification_0001") or dialect not in (
        "sqlite",
        "postgresql",
    ):
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


def create_colocated_notification() -> None:
    """Create or validate notification history co-located with a main owner database."""
    if context.config.attributes.get("colocated_notification") is not True:
        raise RuntimeError("notification history is not configured as co-located")
    connection: Connection = op.get_bind()
    existing: set[str] = set(inspect(connection).get_table_names())
    expected: set[str] = set(RETAINED_NOTIFICATION_TABLES)
    additions: set[str] = set(COLOCATED_NOTIFICATION_TABLES)
    if "alembic_version" in existing:
        legacy_revision: str | None = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()
    else:
        legacy_revision = None
    if legacy_revision == "0010_service_split":
        if not expected.union(additions) <= existing:
            raise RuntimeError("co-located notification schema is incomplete")
        from telescreen.db.adopt import validate_notification_owner_state
        from telescreen.db.schema_check import validate_notification_owner_schema

        validate_notification_owner_schema(connection)
        validate_notification_owner_state(connection)
        return
    if legacy_revision is not None or not expected <= existing or additions.intersection(existing):
        raise RuntimeError("co-located notification schema state is unsupported")
    from telescreen.db.schema_check import validate_notification_owner_schema

    validate_notification_owner_schema(connection, tables=RETAINED_NOTIFICATION_TABLES)
    sql: str = _snapshot("notification_0001", connection.dialect.name)
    for statement in sql.split(";"):
        if not statement.strip():
            continue
        table_match: re.Match[str] | None = re.match(r"\s*CREATE TABLE (\w+) ", statement)
        index_match: re.Match[str] | None = re.search(r" ON (\w+) \(", statement)
        target: str | None = (
            table_match.group(1)
            if table_match is not None
            else index_match.group(1)
            if index_match is not None
            else None
        )
        if target in additions:
            connection.execute(text(statement))
    connection.execute(
        text(
            "INSERT INTO notification_audit_chain_head (id, seq, hash) VALUES (1, 0, :hash)"
        ).bindparams(hash="0" * 64)
    )
    append_only("notification_audit_records")


def drop_colocated_notification() -> None:
    """Drop notification-only additions while retaining the four main baseline tables."""
    connection: Connection = op.get_bind()
    append_only("notification_audit_records", drop=True)
    for table_name in reversed(snapshot_tables("notification_0001", connection.dialect.name)):
        if table_name in COLOCATED_NOTIFICATION_TABLES:
            op.drop_table(table_name)


def append_only(table: str, *, drop: bool = False) -> None:
    """Preserve the append-only policy independently in each owner's database."""
    if table not in (
        "audit_records",
        "data_asset_events",
        "inventory_audit_records",
        "notification_audit_records",
    ):
        raise RuntimeError("unsupported append-only table")
    connection: Connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return
    if table == "audit_records":
        function: str = "telescreen_reject_audit_mutation"
    elif table == "data_asset_events":
        function = "telescreen_reject_data_asset_event_mutation"
    elif table == "inventory_audit_records":
        function = "telescreen_reject_inventory_audit_mutation"
    else:
        function = "telescreen_reject_notification_audit_mutation"
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
