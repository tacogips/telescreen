"""Add the v1.3 issue, alert, notification and integration schema."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import MetaData, Table, text
from sqlalchemy.engine import Connection

from telescreen.db import models as _models

revision: str = "0004_v1_3"
down_revision: str | None = "0003_v1_2"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

NEW_TABLES: tuple[str, ...] = (
    "saved_views",
    "saved_view_stars",
    "saved_view_defaults",
    "issue_comments",
    "issue_mentions",
    "issue_subscriptions",
    "issue_bookmarks",
    "external_issue_links",
    "issue_merges",
    "ownership_rules",
    "alert_rules",
    "alert_fires",
    "notification_settings",
    "integration_checks",
)
NEW_COLUMNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "issues",
        (
            "priority",
            "priority_manual",
            "needs_review",
            "merged_into_id",
            "archive_kind",
            "archive_until",
            "archive_threshold",
            "archive_baseline",
            "archived_at",
        ),
    ),
    ("notifications", ("category",)),
)
ISSUE_INDEXES: tuple[str, ...] = ("ix_issues_merged_into_id", "ix_issues_status_merged")


def upgrade() -> None:
    """Create missing v1.3 schema and backfill newly introduced priority state."""
    bind: Connection = op.get_bind()
    inspector: sa.Inspector = sa.inspect(bind)
    existing_tables: set[str] = set(inspector.get_table_names())
    for table_name in NEW_TABLES:
        if table_name not in existing_tables:
            table: Table = _models.IssueRow.metadata.tables[table_name]
            clone_metadata: MetaData = MetaData()
            cloned: Table = table.to_metadata(clone_metadata)
            op.create_table(table.name, *list(cloned.columns), *list(cloned.constraints))
            existing_tables.add(table_name)

    priority_added: bool = False
    for table_name, column_names in NEW_COLUMNS:
        existing_columns: set[str] = {
            column["name"] for column in sa.inspect(bind).get_columns(table_name)
        }
        for column_name in column_names:
            if column_name not in existing_columns:
                column: sa.Column[str] = _models.IssueRow.metadata.tables[table_name].c[column_name]
                op.add_column(table_name, column._copy())
                existing_columns.add(column_name)
                if table_name == "issues" and column_name == "priority":
                    priority_added = True

    for table_name in (*NEW_TABLES, "issues"):
        table: Table = _models.IssueRow.metadata.tables[table_name]
        existing_indexes: set[str] = {
            str(index["name"])
            for index in sa.inspect(bind).get_indexes(table_name)
            if index["name"] is not None
        }
        for index in table.indexes:
            if index.name is None:
                continue
            if table_name == "issues" and index.name not in ISSUE_INDEXES:
                continue
            if index.name not in existing_indexes:
                index_columns: list[str] = [column.name for column in index.columns]
                op.create_index(index.name, table_name, index_columns, unique=index.unique)
                existing_indexes.add(index.name)

    if priority_added:
        bind.execute(
            text(
                "UPDATE issues SET priority = CASE "
                "WHEN severity IN ('critical','high') THEN 'high' ELSE 'medium' END"
            )
        )
        bind.execute(
            text("UPDATE issues SET needs_review = true WHERE status IN ('unresolved','regressed')")
        )


def downgrade() -> None:
    """Drop only the v1.3 tables, indexes and columns that exist."""
    bind: Connection = op.get_bind()
    inspector: sa.Inspector = sa.inspect(bind)
    existing_tables: set[str] = set(inspector.get_table_names())

    if "issues" in existing_tables:
        existing_indexes: set[str] = {
            str(index["name"])
            for index in sa.inspect(bind).get_indexes("issues")
            if index["name"] is not None
        }
        for index_name in ISSUE_INDEXES:
            if index_name in existing_indexes:
                op.drop_index(index_name, table_name="issues")

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

    for table_name in reversed(NEW_TABLES):
        if table_name in existing_tables:
            op.drop_table(table_name)
