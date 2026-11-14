"""Frozen inventory-owner baseline equivalent to shared revision 0009."""

from alembic import op
from sqlalchemy import text

from telescreen.db.owned_migrations.baseline import append_only, create_baseline, drop_baseline

revision: str = "inventory_0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create only inventory-owned tables, allocation seed and event protection."""
    create_baseline(revision)
    op.execute(text("INSERT INTO inventory_counters (name, value) VALUES ('data_asset_number', 0)"))
    append_only("data_asset_events")


def downgrade() -> None:
    """Remove this owner's baseline without touching main."""
    append_only("data_asset_events", drop=True)
    drop_baseline(revision)
