"""Add producer-owned outbox and inventory-owned ordered command receipts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0006_inventory_handoff"
down_revision: str | None = "0005_v1_4"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLES: tuple[str, ...] = (
    "inventory_outbox_streams",
    "inventory_outbox",
    "inventory_inbox_streams",
    "inventory_inbox",
)


def upgrade() -> None:
    """Create durable handoff storage without changing existing business tables."""
    bind: Connection = op.get_bind()
    tables: set[str] = set(sa.inspect(bind).get_table_names())
    for name in ("inventory_outbox_streams", "inventory_inbox_streams"):
        if name not in tables:
            op.create_table(
                name,
                sa.Column("id", sa.String(256), primary_key=True),
                sa.Column("last_sequence", sa.Integer(), nullable=False),
            )
    if "inventory_outbox" not in tables:
        op.create_table(
            "inventory_outbox",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("stream_id", sa.String(256), nullable=False),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint(
                "stream_id", "sequence", name="uq_inventory_outbox_stream_sequence"
            ),
        )
    if "inventory_inbox" not in tables:
        op.create_table(
            "inventory_inbox",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("stream_id", sa.String(256), primary_key=True),
            sa.Column("sequence", sa.Integer(), primary_key=True),
            sa.Column("payload_sha256", sa.String(64), nullable=False),
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("id", name="uq_inventory_inbox_id"),
        )


def downgrade() -> None:
    """Remove handoff tables; callers must drain pending commands before downgrade."""
    bind: Connection = op.get_bind()
    tables: set[str] = set(sa.inspect(bind).get_table_names())
    for name in reversed(_TABLES):
        if name in tables:
            op.drop_table(name)
