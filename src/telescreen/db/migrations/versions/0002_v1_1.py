"""Add the v1.1 exposure, coverage, policy and identity schema."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import MetaData, Table, text
from sqlalchemy.engine import Connection

from telescreen.db.migrations.frozen import metadata_for

revision: str = "0002_v1_1"
down_revision: str | None = "0001_initial"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

NEW_TABLES: tuple[str, ...] = (
    "placement_policies",
    "issue_exceptions",
    "member_identities",
    "resource_coverage",
    "scan_coverage",
    "collector_health",
)
NEW_COLUMNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "issues",
        (
            "base_severity",
            "exposure_visibility",
            "exposure_json",
            "exposure_changed_at",
            "placement_keys_json",
            "resource_modified_at",
            "owner_ref",
            "policy_name",
            "policy_version",
            "risk_score",
            "asset_tier",
        ),
    ),
    ("findings", ("fingerprint_key_version",)),
    ("teams", ("lead_member_id",)),
    ("police_workers", ("health_alerted_at",)),
)
NEW_INDEX_TABLES: tuple[str, ...] = (
    "issues",
    "findings",
    "placement_policies",
    "issue_exceptions",
    "member_identities",
    "resource_coverage",
    "scan_coverage",
    "collector_health",
)

_metadata: MetaData = metadata_for(2)


def upgrade() -> None:
    """Create only missing v1.1 tables, columns and indexes."""
    bind: Connection = op.get_bind()
    inspector: sa.Inspector = sa.inspect(bind)
    existing_tables: set[str] = set(inspector.get_table_names())
    for table_name in NEW_TABLES:
        if table_name not in existing_tables:
            table: Table = _metadata.tables[table_name]
            clone_metadata: MetaData = MetaData()
            cloned: Table = table.to_metadata(clone_metadata)
            op.create_table(table.name, *list(cloned.columns), *list(cloned.constraints))
            for index in cloned.indexes:
                index_columns: list[str] = [column.name for column in index.columns]
                if len(index_columns) == 1 and cloned.c[index_columns[0]].index:
                    continue
                op.create_index(index.name, table.name, index_columns, unique=index.unique)
            existing_tables.add(table_name)

    base_severity_added: bool = False
    for table_name, column_names in NEW_COLUMNS:
        existing_columns: set[str] = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        for column_name in column_names:
            if column_name not in existing_columns:
                column: sa.Column[str] = _metadata.tables[table_name].c[column_name]
                op.add_column(table_name, column._copy())
                existing_columns.add(column_name)
                if table_name == "issues" and column_name == "base_severity":
                    base_severity_added = True

    if base_severity_added:
        op.execute(text("UPDATE issues SET base_severity = severity"))

    for table_name in NEW_INDEX_TABLES:
        table: Table = _metadata.tables[table_name]
        existing_indexes: set[str] = {
            index["name"] for index in inspector.get_indexes(table_name) if index["name"]
        }
        # The models describe the newest schema; skip indexes on columns that later
        # revisions add, so an upgrade through this revision never references them.
        present_columns: set[str] = {
            column["name"] for column in sa.inspect(bind).get_columns(table_name)
        }
        for index in table.indexes:
            if index.name is None:
                continue
            index_columns: list[str] = [column.name for column in index.columns]
            if index.name not in existing_indexes and set(index_columns) <= present_columns:
                op.create_index(index.name, table_name, index_columns, unique=index.unique)
                existing_indexes.add(index.name)


def downgrade() -> None:
    """Remove v1.1 additions while leaving the v1 schema intact."""
    bind: Connection = op.get_bind()
    inspector: sa.Inspector = sa.inspect(bind)
    existing_tables: set[str] = set(inspector.get_table_names())

    for table_name in NEW_INDEX_TABLES:
        if table_name not in existing_tables:
            continue
        existing_indexes: set[str] = {
            index["name"] for index in inspector.get_indexes(table_name) if index["name"]
        }
        for index in _metadata.tables[table_name].indexes:
            if index.name in existing_indexes and index.name in {
                "ix_issues_status_risk",
                "ix_issues_exposure_visibility",
                "ix_findings_key_fingerprint",
            }:
                op.drop_index(index.name, table_name=table_name)

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
