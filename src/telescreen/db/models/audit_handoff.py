"""Inventory-owned audit publication and audit-owned deduplication storage."""

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from telescreen.db.base import Base


class InventoryAuditOutboxRow(Base, kw_only=True):
    """A masked audit command committed atomically with inventory changes."""

    __tablename__ = "inventory_audit_outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payload_json: Mapped[str] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class AuditReceiptRow(Base, kw_only=True):
    """External event identity and original payload digest, committed with the chain."""

    __tablename__ = "audit_receipts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
