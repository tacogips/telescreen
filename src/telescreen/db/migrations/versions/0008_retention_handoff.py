"""Version metadata values and add durable retention notification handoff storage."""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0008_retention_handoff"
down_revision: str | None = "0007_inventory_audit"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Preserve legacy notification markers and assign each existing value a revision."""
    bind: Connection = op.get_bind()
    columns: set[str] = {
        column["name"] for column in sa.inspect(bind).get_columns("data_field_values")
    }
    if "revision_id" not in columns:
        op.add_column("data_field_values", sa.Column("revision_id", sa.String(36), nullable=True))
    for row in bind.execute(
        sa.text("SELECT asset_id, field_id FROM data_field_values WHERE revision_id IS NULL")
    ):
        bind.execute(
            sa.text(
                "UPDATE data_field_values SET revision_id = :revision "
                "WHERE asset_id = :asset AND field_id = :field AND revision_id IS NULL"
            ),
            {"revision": str(uuid4()), "asset": row[0], "field": row[1]},
        )
    with op.batch_alter_table("data_field_values") as batch:
        batch.alter_column("revision_id", existing_type=sa.String(36), nullable=False)
    tables: set[str] = set(sa.inspect(bind).get_table_names())
    if "inventory_retention_outbox" not in tables:
        op.create_table(
            "inventory_retention_outbox",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "retention_notification_receipts" not in tables:
        op.create_table(
            "retention_notification_receipts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("payload_sha256", sa.String(64), nullable=False),
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    """Require stopped writers and a drained notification-intent outbox."""
    bind: Connection = op.get_bind()
    tables: set[str] = set(sa.inspect(bind).get_table_names())
    if "inventory_retention_outbox" in tables:
        pending: int = bind.execute(
            sa.text("SELECT count(*) FROM inventory_retention_outbox WHERE delivered_at IS NULL")
        ).scalar_one()
        if pending:
            raise RuntimeError("drain inventory retention outbox before downgrade")
    for name in ("inventory_retention_outbox", "retention_notification_receipts"):
        if name in tables:
            op.drop_table(name)
    columns: set[str] = {
        column["name"] for column in sa.inspect(bind).get_columns("data_field_values")
    }
    if "revision_id" in columns:
        with op.batch_alter_table("data_field_values") as batch:
            batch.drop_column("revision_id")
