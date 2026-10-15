"""Frozen notification-owner baseline and audit chain genesis."""

from alembic import op
from sqlalchemy import text

from telescreen.db.owned_migrations.baseline import append_only, create_baseline, drop_baseline

revision: str = "notification_0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create notification-owned tables, seed its audit head and install protection."""
    create_baseline(revision)
    op.execute(
        text(
            "INSERT INTO notification_audit_chain_head (id, seq, hash) VALUES (1, 0, :hash)"
        ).bindparams(hash="0" * 64)
    )
    append_only("notification_audit_records")


def downgrade() -> None:
    """Remove the notification baseline and its append-only protection."""
    append_only("notification_audit_records", drop=True)
    drop_baseline(revision)
