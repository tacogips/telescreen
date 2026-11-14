"""Add the v1.2 notification template, notification metadata and member roles."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import MetaData, Table, text
from sqlalchemy.engine import Connection, Result

from telescreen.db import models as _models

revision: str = "0003_v1_2"
down_revision: str | None = "0002_v1_1"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

NEW_TABLES: tuple[str, ...] = ("notification_templates",)
NEW_COLUMNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "notifications",
        (
            "event_kind",
            "template_name",
            "template_version",
            "set_name",
            "target_index",
            "error_code",
        ),
    ),
    ("members", ("role", "last_sign_in_at")),
    ("team_memberships", ("origin",)),
)
NEW_INDEXES: tuple[tuple[str, str], ...] = (
    ("notification_templates", "ix_notification_templates_name_active"),
    ("members", "ix_members_role_active"),
)


def upgrade() -> None:
    """Create only missing v1.2 tables, columns and indexes, then backfill roles."""
    bind: Connection = op.get_bind()
    inspector: sa.Inspector = sa.inspect(bind)
    existing_tables: set[str] = set(inspector.get_table_names())
    for table_name in NEW_TABLES:
        if table_name not in existing_tables:
            table: Table = _models.AuditRecordRow.metadata.tables[table_name]
            clone_metadata: MetaData = MetaData()
            cloned: Table = table.to_metadata(clone_metadata)
            op.create_table(table.name, *list(cloned.columns), *list(cloned.constraints))
            existing_tables.add(table_name)

    role_added: bool = False
    for table_name, column_names in NEW_COLUMNS:
        existing_columns: set[str] = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        for column_name in column_names:
            if column_name not in existing_columns:
                column: sa.Column[str] = _models.AuditRecordRow.metadata.tables[table_name].c[
                    column_name
                ]
                op.add_column(table_name, column._copy())
                existing_columns.add(column_name)
                if table_name == "members" and column_name == "role":
                    role_added = True

    for table_name, index_name in NEW_INDEXES:
        existing_indexes: set[str] = {
            index["name"] for index in inspector.get_indexes(table_name) if index["name"]
        }
        if index_name not in existing_indexes:
            index: sa.Index = next(
                index
                for index in _models.AuditRecordRow.metadata.tables[table_name].indexes
                if index.name == index_name
            )
            index_columns: list[str] = [column.name for column in index.columns]
            op.create_index(index_name, table_name, index_columns, unique=index.unique)

    if role_added:
        _backfill_member_roles(bind)


def _backfill_member_roles(bind: Connection) -> None:
    """Set each member's role to the highest scope of its active bound tokens."""
    member_rows: Result[tuple[str]] = bind.execute(text("SELECT id FROM members"))
    for member_row in member_rows:
        member_id: str = member_row[0]
        token_rows: Result[tuple[str]] = bind.execute(
            text(
                "SELECT scopes FROM api_tokens WHERE member_id = :member_id AND revoked_at IS NULL"
            ),
            {"member_id": member_id},
        )
        has_admin: bool = False
        has_write: bool = False
        has_read: bool = False
        for token_row in token_rows:
            scopes: set[str] = set(token_row[0].split(","))
            has_admin = has_admin or "admin" in scopes
            has_write = has_write or "write" in scopes
            has_read = has_read or "read" in scopes
        member_role: str | None = None
        if has_admin:
            member_role = "admin"
        elif has_write:
            member_role = "responder"
        elif has_read:
            member_role = "viewer"
        if member_role is not None:
            bind.execute(
                text("UPDATE members SET role = :role WHERE id = :member_id"),
                {"role": member_role, "member_id": member_id},
            )


def downgrade() -> None:
    """Remove v1.2 indexes, columns and table when present."""
    bind: Connection = op.get_bind()
    inspector: sa.Inspector = sa.inspect(bind)
    existing_tables: set[str] = set(inspector.get_table_names())

    for table_name, index_name in NEW_INDEXES:
        if table_name not in existing_tables:
            continue
        existing_indexes: set[str] = {
            index["name"] for index in inspector.get_indexes(table_name) if index["name"]
        }
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name=table_name)

    for table_name, column_names in NEW_COLUMNS:
        if table_name not in existing_tables:
            continue
        existing_columns: set[str] = {
            column["name"] for column in inspector.get_columns(table_name)
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

    for table_name in reversed(NEW_TABLES):
        if table_name in existing_tables:
            op.drop_table(table_name)
