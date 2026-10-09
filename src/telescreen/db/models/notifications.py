"""Outbound notification database model."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from telescreen.db.base import Base


class NotificationRow(Base, kw_only=True):
    """A masked notification with a DLP verdict and delivery state."""

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    channel: Mapped[str] = mapped_column(String(32))
    recipient_ref: Mapped[str] = mapped_column(String(256))
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    dlp_verdict: Mapped[str] = mapped_column(String(32))
    dlp_hit_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), index=True)
    delivery_attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    issue_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
