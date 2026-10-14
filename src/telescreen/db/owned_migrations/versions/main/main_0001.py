"""Frozen main-owner baseline equivalent to shared revision 0009."""

from alembic import op
from sqlalchemy import text

from telescreen.db.owned_migrations.baseline import append_only, create_baseline, drop_baseline

revision: str = "main_0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create only main-owned tables, seeds and the audit protection trigger."""
    create_baseline(revision)
    op.execute(text("INSERT INTO counters (name, value) VALUES ('issue_number', 0)"))
    op.execute(
        text("INSERT INTO audit_chain_head (id, seq, hash) VALUES (1, 0, :hash)").bindparams(
            hash="0" * 64
        )
    )
    append_only("audit_records")


def downgrade() -> None:
    """Remove this owner's baseline without touching inventory."""
    append_only("audit_records", drop=True)
    drop_baseline(revision)
