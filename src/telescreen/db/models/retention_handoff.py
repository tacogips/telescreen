"""Inventory retention publication and notification-owned deduplication models."""

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from telescreen.db.base import Base


class InventoryRetentionOutboxRow(Base, kw_only=True):
    """One immutable due-date revision committed with the inventory publication marker."""

    __tablename__ = "inventory_retention_outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payload_json: Mapped[str] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class RetentionNotificationReceiptRow(Base, kw_only=True):
    """An external obligation ID and digest committed with notification intents."""

    __tablename__ = "retention_notification_receipts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
