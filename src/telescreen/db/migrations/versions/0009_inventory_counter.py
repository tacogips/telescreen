"""Move asset number allocation into inventory-owned persistence without renumbering."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0009_inventory_counter"
down_revision: str | None = "0008_retention_handoff"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _maximum(bind: Connection, table: str) -> int:
    """Read the frozen allocation high-water mark from an existing counter table."""
    return int(
        bind.execute(
            sa.text(f"SELECT COALESCE(MAX(value), 0) FROM {table} WHERE name = 'data_asset_number'")
        ).scalar_one()
    )


def _merge(bind: Connection, table: str, value: int) -> None:
    """Preserve whichever high-water mark is greater; never reset an existing sequence."""
    bind.execute(
        sa.text(
            f"INSERT INTO {table} (name, value) SELECT 'data_asset_number', :value "
            f"WHERE NOT EXISTS (SELECT 1 FROM {table} WHERE name = 'data_asset_number')"
        ),
        {"value": value},
    )
    bind.execute(
        sa.text(
            f"UPDATE {table} SET value = :value WHERE name = 'data_asset_number' AND value < :value"
        ),
        {"value": value},
    )


def upgrade() -> None:
    """Stop writers during cutover; preserve legacy allocation and existing asset IDs."""
    bind: Connection = op.get_bind()
    tables: set[str] = set(sa.inspect(bind).get_table_names())
    if "inventory_counters" not in tables:
        op.create_table(
            "inventory_counters",
            sa.Column("name", sa.String(64), primary_key=True),
            sa.Column("value", sa.Integer(), nullable=False),
        )
    asset_max: int = int(
        bind.execute(sa.text("SELECT COALESCE(MAX(number), 0) FROM data_assets")).scalar_one()
    )
    legacy_max: int = _maximum(bind, "counters") if "counters" in tables else 0
    _merge(bind, "inventory_counters", max(asset_max, legacy_max))


def downgrade() -> None:
    """Stop writers and return the high-water mark to the legacy allocator before removal."""
    bind: Connection = op.get_bind()
    tables: set[str] = set(sa.inspect(bind).get_table_names())
    if "inventory_counters" not in tables:
        return
    if "counters" not in tables:
        raise RuntimeError("legacy counter storage is required before inventory counter downgrade")
    _merge(bind, "counters", _maximum(bind, "inventory_counters"))
    op.drop_table("inventory_counters")
