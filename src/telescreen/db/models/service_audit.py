"""Independent audit chains for Inventory and Notification stores."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from telescreen.db.base import Base
from telescreen.db.models.audit import AuditChainHeadRow, AuditRecordRow


class InventoryAuditRecordRow(Base, kw_only=True):
    """A sequence-numbered Inventory audit record."""

    __tablename__ = "inventory_audit_records"
    __table_args__ = (UniqueConstraint("id", name="uq_inventory_audit_records_id"),)

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    id: Mapped[str] = mapped_column(String(36))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    actor_kind: Mapped[str] = mapped_column(String(32))
    actor_ref: Mapped[str] = mapped_column(String(256))
    action: Mapped[str] = mapped_column(String(128))
    target_kind: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str] = mapped_column(String(36))
    summary: Mapped[str] = mapped_column(Text)
    details_json: Mapped[str] = mapped_column(Text, repr=False)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class InventoryAuditChainHeadRow(Base, kw_only=True):
    """Current Inventory audit chain sequence and hash."""

    __tablename__ = "inventory_audit_chain_head"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer)
    hash: Mapped[str] = mapped_column(String(64))


class NotificationAuditRecordRow(Base, kw_only=True):
    """A sequence-numbered Notification audit record."""

    __tablename__ = "notification_audit_records"
    __table_args__ = (UniqueConstraint("id", name="uq_notification_audit_records_id"),)

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    id: Mapped[str] = mapped_column(String(36))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    actor_kind: Mapped[str] = mapped_column(String(32))
    actor_ref: Mapped[str] = mapped_column(String(256))
    action: Mapped[str] = mapped_column(String(128))
    target_kind: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str] = mapped_column(String(36))
    summary: Mapped[str] = mapped_column(Text)
    details_json: Mapped[str] = mapped_column(Text, repr=False)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class NotificationAuditChainHeadRow(Base, kw_only=True):
    """Current Notification audit chain sequence and hash."""

    __tablename__ = "notification_audit_chain_head"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer)
    hash: Mapped[str] = mapped_column(String(64))


type AuditRecordModel = (
    type[AuditRecordRow] | type[InventoryAuditRecordRow] | type[NotificationAuditRecordRow]
)
type AuditHeadModel = (
    type[AuditChainHeadRow] | type[InventoryAuditChainHeadRow] | type[NotificationAuditChainHeadRow]
)
