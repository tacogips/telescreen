"""Data inventory asset, event, item, field and value database models."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from telescreen.db.base import Base


class DataAssetRow(Base, kw_only=True):
    """A registered or discovered data location."""

    __tablename__ = "data_assets"
    __table_args__ = (
        Index("ix_data_assets_source", "source"),
        Index("ix_data_assets_config_state", "collector_config_id", "state", "last_seen_at"),
        Index("ix_data_assets_state_last_seen", "state", "last_seen_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    number: Mapped[int] = mapped_column(Integer, unique=True)
    resource_key: Mapped[str] = mapped_column(String(64), unique=True)
    source: Mapped[str] = mapped_column(String(64))
    origin: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(512))
    display_locator: Mapped[str] = mapped_column(Text)
    collector_config_id: Mapped[str | None] = mapped_column(String(36), default=None)
    sweep_covered: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    item_count: Mapped[int | None] = mapped_column(Integer, default=None)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    overdue_notified_for: Mapped[str | None] = mapped_column(String(10), default=None)
    created_by: Mapped[str | None] = mapped_column(String(256), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DataAssetEventRow(Base, kw_only=True):
    """An append-only lifecycle, item, or metadata event for an asset."""

    __tablename__ = "data_asset_events"
    __table_args__ = (Index("ix_data_asset_events_asset_recorded", "asset_id", "recorded_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_ref: Mapped[str] = mapped_column(String(256))
    scan_job_id: Mapped[str | None] = mapped_column(String(36), default=None)
    items_added: Mapped[int | None] = mapped_column(Integer, default=None)
    items_removed: Mapped[int | None] = mapped_column(Integer, default=None)
    item_count_after: Mapped[int | None] = mapped_column(Integer, default=None)
    values_added: Mapped[int | None] = mapped_column(Integer, default=None)
    values_removed: Mapped[int | None] = mapped_column(Integer, default=None)
    value_count_after: Mapped[int | None] = mapped_column(Integer, default=None)
    field_key: Mapped[str | None] = mapped_column(String(48), default=None)
    old_value: Mapped[str | None] = mapped_column(Text, default=None)
    new_value: Mapped[str | None] = mapped_column(Text, default=None)
    note: Mapped[str | None] = mapped_column(Text, default=None)


class DataAssetItemRow(Base, kw_only=True):
    """A keyed item presence record containing only its HMAC fingerprint."""

    __tablename__ = "data_asset_items"
    __table_args__ = (
        UniqueConstraint("asset_id", "kind", "fingerprint", name="uq_data_asset_items_key"),
        Index("ix_data_asset_items_asset_state", "asset_id", "kind", "removed_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    asset_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(8))
    fingerprint: Mapped[str] = mapped_column(String(64))
    key_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class DataFieldRow(Base, kw_only=True):
    """An administrator-defined metadata field."""

    __tablename__ = "data_fields"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    key: Mapped[str] = mapped_column(String(48), unique=True)
    label: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(String(500), default=None)
    field_type: Mapped[str] = mapped_column(String(16))
    options_json: Mapped[str | None] = mapped_column(Text, default=None)
    required: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    position: Mapped[int] = mapped_column(Integer)
    role: Mapped[str | None] = mapped_column(String(16), default=None)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_by: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DataFieldValueRow(Base, kw_only=True):
    """A validated value for one asset and one metadata field."""

    __tablename__ = "data_field_values"
    __table_args__ = (Index("ix_data_field_values_field_date", "field_id", "value_date"),)

    asset_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    field_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text)
    value_date: Mapped[str | None] = mapped_column(String(10), default=None)
    updated_by: Mapped[str] = mapped_column(String(256))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
