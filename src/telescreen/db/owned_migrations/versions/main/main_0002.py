"""Add main-owned notification outbox and projection change tables."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

revision: str = "main_0002"
down_revision: str | None = "main_0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add main-owned notification outbox and projection change tables."""
    bind: Connection = op.get_bind()
    if "alembic_version" in inspect(bind).get_table_names():
        return
    tables: set[str] = set(inspect(bind).get_table_names())
    if "notification_outbox" not in tables:
        op.create_table(
            "notification_outbox",
            sa.Column("id", sa.String(36), nullable=False, primary_key=True),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "projection_changes" not in tables:
        op.create_table(
            "projection_changes",
            sa.Column("id", sa.String(36), nullable=False, primary_key=True),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("entity_ref", sa.String(64), nullable=False),
            sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        )
    op.execute(
        text(
            "INSERT INTO counters (name, value) "
            "SELECT 'directory_revision', 0 WHERE NOT EXISTS "
            "(SELECT 1 FROM counters WHERE name = 'directory_revision')"
        )
    )


def downgrade() -> None:
    """Remove only the schema introduced by main_0002."""
    bind: Connection = op.get_bind()
    if "alembic_version" in inspect(bind).get_table_names():
        return
    tables: set[str] = set(inspect(bind).get_table_names())
    if "projection_changes" in tables:
        op.drop_table("projection_changes")
    if "notification_outbox" in tables:
        op.drop_table("notification_outbox")
