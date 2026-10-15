"""Notification command delivery and receipt persistence models."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from telescreen.db.base import Base


class NotificationOutboxRow(Base, kw_only=True):
    """A Telescreen notification command awaiting delivery."""

    __tablename__ = "notification_outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payload_json: Mapped[str] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class InventoryNotificationOutboxRow(Base, kw_only=True):
    """An inventory notification command awaiting delivery."""

    __tablename__ = "inventory_notification_outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payload_json: Mapped[str] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class NotificationInboxRow(Base, kw_only=True):
    """An idempotent notification command receipt."""

    __tablename__ = "notification_inbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationDeliveryStateRow(Base, kw_only=True):
    """Retry schedule and lease for one persisted notification."""

    __tablename__ = "notification_delivery_state"

    notification_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    attempt: Mapped[int] = mapped_column(Integer)
    max_attempts: Mapped[int] = mapped_column(Integer)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
