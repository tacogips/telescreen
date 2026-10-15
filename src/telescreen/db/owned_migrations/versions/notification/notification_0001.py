"""Frozen notification-owner baseline and audit chain genesis."""

from alembic import context, op
from sqlalchemy import inspect, text

from telescreen.db.owned_migrations.baseline import (
    append_only,
    create_baseline,
    create_colocated_notification,
    drop_baseline,
    drop_colocated_notification,
)

revision: str = "notification_0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create notification-owned tables, seed its audit head and install protection."""
    if context.config.attributes.get("colocated_notification") is True:
        create_colocated_notification()
        return
    create_baseline(revision)
    op.execute(
        text(
            "INSERT INTO notification_audit_chain_head (id, seq, hash) VALUES (1, 0, :hash)"
        ).bindparams(hash="0" * 64)
    )
    append_only("notification_audit_records")


def downgrade() -> None:
    """Remove the notification baseline and its append-only protection."""
    if "alembic_version_main" in inspect(op.get_bind()).get_table_names():
        drop_colocated_notification()
        return
    append_only("notification_audit_records", drop=True)
    drop_baseline(revision)
