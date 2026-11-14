"""Create the complete v1 schema and initialize shared database counters."""

from alembic import op
from sqlalchemy import MetaData, Table, text
from sqlalchemy.engine import Connection

from telescreen.db import models as _models

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create all declared tables, indexes and database seed rows."""
    bind: Connection = op.get_bind()
    for table in _models.AuditRecordRow.metadata.sorted_tables:
        clone_metadata: MetaData = MetaData()
        cloned: Table = table.to_metadata(clone_metadata)
        op.create_table(table.name, *list(cloned.columns), *list(cloned.constraints))
        for index in cloned.indexes:
            index_columns: list[str] = [column.name for column in index.columns]
            if len(index_columns) == 1 and cloned.c[index_columns[0]].index:
                continue
            op.create_index(
                index.name,
                table.name,
                index_columns,
                unique=index.unique,
            )
    op.bulk_insert(
        _models.AuditRecordRow.metadata.tables["counters"],
        [{"name": "issue_number", "value": 0}],
    )
    op.bulk_insert(
        _models.AuditRecordRow.metadata.tables["audit_chain_head"],
        [{"id": 1, "seq": 0, "hash": "0" * 64}],
    )
    if bind.dialect.name == "postgresql":
        op.execute(
            text(
                "CREATE FUNCTION telescreen_reject_audit_mutation() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'audit_records is append-only'; "
                "END; $$"
            )
        )
        op.execute(
            text(
                "CREATE TRIGGER audit_records_append_only BEFORE UPDATE OR DELETE "
                "ON audit_records FOR EACH ROW EXECUTE FUNCTION telescreen_reject_audit_mutation()"
            )
        )


def downgrade() -> None:
    """Remove the audit trigger and all v1 tables."""
    bind: Connection = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(text("DROP TRIGGER IF EXISTS audit_records_append_only ON audit_records"))
        op.execute(text("DROP FUNCTION IF EXISTS telescreen_reject_audit_mutation()"))
    for table in reversed(_models.AuditRecordRow.metadata.sorted_tables):
        op.drop_table(table.name)
