"""Add inventory projection and audit tables."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

revision: str = "inventory_0002"
down_revision: str | None = "inventory_0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add inventory projection and audit tables."""
    bind: Connection = op.get_bind()
    if "alembic_version" in inspect(bind).get_table_names():
        return
    op.create_table(
        "inventory_audit_records",
        sa.Column("seq", sa.Integer(), nullable=False, primary_key=True, autoincrement=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_kind", sa.String(32), nullable=False),
        sa.Column("actor_ref", sa.String(256), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("target_kind", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("id", name="uq_inventory_audit_records_id"),
    )
    op.create_index(
        "ix_inventory_audit_records_at", "inventory_audit_records", ["at"], unique=False
    )
    op.create_table(
        "inventory_audit_chain_head",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
    )
    op.create_table(
        "inventory_directory_entries",
        sa.Column("kind", sa.String(32), nullable=False, primary_key=True),
        sa.Column("id", sa.String(36), nullable=False, primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("present", sa.Boolean(), nullable=False),
        sa.Column("lookup_key", sa.String(128), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_inventory_directory_entries_lookup",
        "inventory_directory_entries",
        ["kind", "lookup_key"],
        unique=False,
    )
    op.create_table(
        "inventory_issue_links",
        sa.Column("issue_id", sa.String(36), nullable=False, primary_key=True),
        sa.Column("resource_key", sa.String(64), nullable=False),
        sa.Column("short_id", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("owner_ref", sa.String(256), nullable=True),
    )
    op.create_index(
        "ix_inventory_issue_links_resource_key",
        "inventory_issue_links",
        ["resource_key"],
        unique=False,
    )
    op.create_table(
        "inventory_notification_outbox",
        sa.Column("id", sa.String(36), nullable=False, primary_key=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    bind: Connection = op.get_bind()
    bind.execute(
        text(
            "INSERT INTO inventory_audit_chain_head (id, seq, hash) VALUES (1, 0, :hash)"
        ).bindparams(hash="0" * 64)
    )
    bind: Connection = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            text(
                "CREATE OR REPLACE FUNCTION telescreen_reject_inventory_audit_mutation() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
                "'inventory_audit_records is append-only'; END; $$"
            )
        )
        op.execute(
            text(
                "DROP TRIGGER IF EXISTS inventory_audit_records_append_only "
                "ON inventory_audit_records"
            )
        )
        op.execute(
            text(
                "CREATE TRIGGER inventory_audit_records_append_only BEFORE UPDATE OR DELETE "
                "ON inventory_audit_records FOR EACH ROW EXECUTE FUNCTION "
                "telescreen_reject_inventory_audit_mutation()"
            )
        )


def downgrade() -> None:
    """Remove only the schema introduced by inventory_0002."""
    bind: Connection = op.get_bind()
    if "alembic_version" in inspect(bind).get_table_names():
        return
    op.drop_table("inventory_notification_outbox")
    op.drop_table("inventory_issue_links")
    op.drop_table("inventory_directory_entries")
    op.drop_table("inventory_audit_chain_head")
    op.drop_table("inventory_audit_records")
    if op.get_bind().dialect.name == "postgresql":
        op.execute(text("DROP FUNCTION IF EXISTS telescreen_reject_inventory_audit_mutation()"))
