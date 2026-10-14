"""Separately owned producer outbox and inventory inbox persistence."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from telescreen.db.base import Base


class InventoryOutboxStreamRow(Base, kw_only=True):
    """Producer-local allocator for each ordered inventory command stream."""

    __tablename__ = "inventory_outbox_streams"

    id: Mapped[str] = mapped_column(String(256), primary_key=True)
    last_sequence: Mapped[int] = mapped_column(Integer)


class InventoryOutboxRow(Base, kw_only=True):
    """Command committed atomically with the producing business transaction."""

    __tablename__ = "inventory_outbox"
    __table_args__ = (
        UniqueConstraint("stream_id", "sequence", name="uq_inventory_outbox_stream_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    stream_id: Mapped[str] = mapped_column(String(256))
    sequence: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[str] = mapped_column(Text, repr=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class InventoryInboxStreamRow(Base, kw_only=True):
    """Inventory-owned cursor protecting the next applicable stream position."""

    __tablename__ = "inventory_inbox_streams"

    id: Mapped[str] = mapped_column(String(256), primary_key=True)
    last_sequence: Mapped[int] = mapped_column(Integer)


class InventoryInboxRow(Base, kw_only=True):
    """Inventory-owned receipt committed with the command's inventory changes."""

    __tablename__ = "inventory_inbox"
    __table_args__ = (UniqueConstraint("id", name="uq_inventory_inbox_id"),)

    id: Mapped[str] = mapped_column(String(36))
    stream_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
