"""Add inventory audit publication and audit-owned idempotent receipts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0007_inventory_audit"
down_revision: str | None = "0006_inventory_handoff"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add handoff tables without changing existing inventory or audit records."""
    bind: Connection = op.get_bind()
    tables: set[str] = set(sa.inspect(bind).get_table_names())
    if "inventory_audit_outbox" not in tables:
        op.create_table(
            "inventory_audit_outbox",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "audit_receipts" not in tables:
        op.create_table(
            "audit_receipts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("payload_sha256", sa.String(64), nullable=False),
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    """Require a drained outbox; remove receipts only after writers are stopped."""
    bind: Connection = op.get_bind()
    tables: set[str] = set(sa.inspect(bind).get_table_names())
    if "inventory_audit_outbox" in tables:
        pending: int = bind.execute(
            sa.text("SELECT count(*) FROM inventory_audit_outbox WHERE delivered_at IS NULL")
        ).scalar_one()
        if pending:
            raise RuntimeError("drain inventory audit outbox before downgrade")
    for name in ("inventory_audit_outbox", "audit_receipts"):
        if name in tables:
            op.drop_table(name)
