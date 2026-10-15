"""Create service split projection handoff and audit tables."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection

revision: str = "0010_service_split"
down_revision: str | None = "0009_inventory_counter"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create service split projection handoff and audit tables."""
    bind: Connection = op.get_bind()
    inspector: sa.Inspector = sa.inspect(bind)
    tables: set[str] = set(inspector.get_table_names())
    if "notification_outbox" not in tables:
        op.create_table(
            "notification_outbox",
            sa.Column("id", sa.String(36), nullable=False, primary_key=True),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "inventory_notification_outbox" not in tables:
        op.create_table(
            "inventory_notification_outbox",
            sa.Column("id", sa.String(36), nullable=False, primary_key=True),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "notification_inbox" not in tables:
        op.create_table(
            "notification_inbox",
            sa.Column("id", sa.String(36), nullable=False, primary_key=True),
            sa.Column("payload_sha256", sa.String(64), nullable=False),
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "notification_delivery_state" not in tables:
        op.create_table(
            "notification_delivery_state",
            sa.Column("notification_id", sa.String(36), nullable=False, primary_key=True),
            sa.Column("attempt", sa.Integer(), nullable=False),
            sa.Column("max_attempts", sa.Integer(), nullable=False),
            sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(
            "ix_notification_delivery_state_available_at",
            "notification_delivery_state",
            ["available_at"],
            unique=False,
        )
    if "projection_changes" not in tables:
        op.create_table(
            "projection_changes",
            sa.Column("id", sa.String(36), nullable=False, primary_key=True),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("entity_ref", sa.String(64), nullable=False),
            sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "inventory_directory_entries" not in tables:
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
    if "notification_directory_entries" not in tables:
        op.create_table(
            "notification_directory_entries",
            sa.Column("kind", sa.String(32), nullable=False, primary_key=True),
            sa.Column("id", sa.String(36), nullable=False, primary_key=True),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("present", sa.Boolean(), nullable=False),
            sa.Column("lookup_key", sa.String(128), nullable=True),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_notification_directory_entries_lookup",
            "notification_directory_entries",
            ["kind", "lookup_key"],
            unique=False,
        )
    if "inventory_issue_links" not in tables:
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
    if "inventory_audit_records" not in tables:
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
    if "inventory_audit_chain_head" not in tables:
        op.create_table(
            "inventory_audit_chain_head",
            sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
            sa.Column("seq", sa.Integer(), nullable=False),
            sa.Column("hash", sa.String(64), nullable=False),
        )
    if "notification_audit_records" not in tables:
        op.create_table(
            "notification_audit_records",
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
            sa.UniqueConstraint("id", name="uq_notification_audit_records_id"),
        )
        op.create_index(
            "ix_notification_audit_records_at", "notification_audit_records", ["at"], unique=False
        )
    if "notification_audit_chain_head" not in tables:
        op.create_table(
            "notification_audit_chain_head",
            sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
            sa.Column("seq", sa.Integer(), nullable=False),
            sa.Column("hash", sa.String(64), nullable=False),
        )
    inspector = sa.inspect(bind)
    if "ix_notification_delivery_state_available_at" not in {
        item["name"] for item in inspector.get_indexes("notification_delivery_state")
    }:
        op.create_index(
            "ix_notification_delivery_state_available_at",
            "notification_delivery_state",
            ["available_at"],
            unique=False,
        )
    if "ix_inventory_directory_entries_lookup" not in {
        item["name"] for item in inspector.get_indexes("inventory_directory_entries")
    }:
        op.create_index(
            "ix_inventory_directory_entries_lookup",
            "inventory_directory_entries",
            ["kind", "lookup_key"],
            unique=False,
        )
    if "ix_notification_directory_entries_lookup" not in {
        item["name"] for item in inspector.get_indexes("notification_directory_entries")
    }:
        op.create_index(
            "ix_notification_directory_entries_lookup",
            "notification_directory_entries",
            ["kind", "lookup_key"],
            unique=False,
        )
    if "ix_inventory_issue_links_resource_key" not in {
        item["name"] for item in inspector.get_indexes("inventory_issue_links")
    }:
        op.create_index(
            "ix_inventory_issue_links_resource_key",
            "inventory_issue_links",
            ["resource_key"],
            unique=False,
        )
    if "ix_inventory_audit_records_at" not in {
        item["name"] for item in inspector.get_indexes("inventory_audit_records")
    }:
        op.create_index(
            "ix_inventory_audit_records_at", "inventory_audit_records", ["at"], unique=False
        )
    if "ix_notification_audit_records_at" not in {
        item["name"] for item in inspector.get_indexes("notification_audit_records")
    }:
        op.create_index(
            "ix_notification_audit_records_at", "notification_audit_records", ["at"], unique=False
        )
    bind.execute(
        text(
            "INSERT INTO counters (name, value) "
            "SELECT 'directory_revision', 0 WHERE NOT EXISTS "
            "(SELECT 1 FROM counters WHERE name = 'directory_revision')"
        )
    )
    bind.execute(
        text(
            "INSERT INTO inventory_audit_chain_head (id, seq, hash) "
            "SELECT 1, 0, :hash WHERE NOT EXISTS "
            "(SELECT 1 FROM inventory_audit_chain_head WHERE id = 1)"
        ).bindparams(hash="0" * 64)
    )
    bind.execute(
        text(
            "INSERT INTO notification_audit_chain_head (id, seq, hash) "
            "SELECT 1, 0, :hash WHERE NOT EXISTS "
            "(SELECT 1 FROM notification_audit_chain_head WHERE id = 1)"
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
    bind: Connection = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            text(
                "CREATE OR REPLACE FUNCTION telescreen_reject_notification_audit_mutation() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
                "'notification_audit_records is append-only'; END; $$"
            )
        )
        op.execute(
            text(
                "DROP TRIGGER IF EXISTS notification_audit_records_append_only "
                "ON notification_audit_records"
            )
        )
        op.execute(
            text(
                "CREATE TRIGGER notification_audit_records_append_only BEFORE UPDATE OR DELETE "
                "ON notification_audit_records FOR EACH ROW EXECUTE FUNCTION "
                "telescreen_reject_notification_audit_mutation()"
            )
        )


def downgrade() -> None:
    """Remove only the schema introduced by 0010_service_split."""
    bind: Connection = op.get_bind()
    tables: set[str] = set(sa.inspect(bind).get_table_names())
    if "notification_audit_chain_head" in tables:
        op.drop_table("notification_audit_chain_head")
    if "notification_audit_records" in tables:
        op.drop_table("notification_audit_records")
    if "inventory_audit_chain_head" in tables:
        op.drop_table("inventory_audit_chain_head")
    if "inventory_audit_records" in tables:
        op.drop_table("inventory_audit_records")
    if "inventory_issue_links" in tables:
        op.drop_table("inventory_issue_links")
    if "notification_directory_entries" in tables:
        op.drop_table("notification_directory_entries")
    if "inventory_directory_entries" in tables:
        op.drop_table("inventory_directory_entries")
    if "projection_changes" in tables:
        op.drop_table("projection_changes")
    if "notification_delivery_state" in tables:
        op.drop_table("notification_delivery_state")
    if "notification_inbox" in tables:
        op.drop_table("notification_inbox")
    if "inventory_notification_outbox" in tables:
        op.drop_table("inventory_notification_outbox")
    if "notification_outbox" in tables:
        op.drop_table("notification_outbox")
    if bind.dialect.name == "postgresql":
        op.execute(text("DROP FUNCTION IF EXISTS telescreen_reject_inventory_audit_mutation()"))
    if bind.dialect.name == "postgresql":
        op.execute(text("DROP FUNCTION IF EXISTS telescreen_reject_notification_audit_mutation()"))
