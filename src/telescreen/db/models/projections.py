"""Directory and issue projection persistence models."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from telescreen.db.base import Base
from telescreen.db.models.inventory_links import InventoryIssueLinkRow


class ProjectionChangeRow(Base, kw_only=True):
    """A durable marker that a projected entity changed."""

    __tablename__ = "projection_changes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    entity_ref: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InventoryDirectoryEntryRow(Base, kw_only=True):
    """Latest masked directory projection row owned by Inventory."""

    __tablename__ = "inventory_directory_entries"
    __table_args__ = (Index("ix_inventory_directory_entries_lookup", "kind", "lookup_key"),)

    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    present: Mapped[bool] = mapped_column(Boolean)
    lookup_key: Mapped[str | None] = mapped_column(String(128), default=None)
    payload_json: Mapped[str] = mapped_column(Text, repr=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationDirectoryEntryRow(Base, kw_only=True):
    """Latest masked directory projection row owned by Notification."""

    __tablename__ = "notification_directory_entries"
    __table_args__ = (Index("ix_notification_directory_entries_lookup", "kind", "lookup_key"),)

    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    present: Mapped[bool] = mapped_column(Boolean)
    lookup_key: Mapped[str | None] = mapped_column(String(128), default=None)
    payload_json: Mapped[str] = mapped_column(Text, repr=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


type DirectoryEntryModel = type[InventoryDirectoryEntryRow] | type[NotificationDirectoryEntryRow]
