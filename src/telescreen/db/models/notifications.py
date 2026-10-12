"""Outbound notification database model."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
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

    event_kind: Mapped[str | None] = mapped_column(String(32), default=None)
    template_name: Mapped[str | None] = mapped_column(String(64), default=None)
    template_version: Mapped[int | None] = mapped_column(Integer, default=None)
    set_name: Mapped[str | None] = mapped_column(String(64), default=None)
    target_index: Mapped[int | None] = mapped_column(Integer, default=None)
    error_code: Mapped[str | None] = mapped_column(String(32), default=None)
    category: Mapped[str | None] = mapped_column(String(32), default=None)


class NotificationTemplateRow(Base, kw_only=True):
    """An immutable version of a named notification template."""

    __tablename__ = "notification_templates"
    __table_args__ = (
        UniqueConstraint("name", "version"),
        Index("ix_notification_templates_name_active", "name", "active"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    event_kind: Mapped[str] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(String(200), default=None)
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    email_subject: Mapped[str | None] = mapped_column(Text, default=None)
    email_body: Mapped[str | None] = mapped_column(Text, default=None)
    slack_body: Mapped[str | None] = mapped_column(Text, default=None)
    active: Mapped[bool] = mapped_column(Boolean)
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationSettingRow(Base, kw_only=True):
    """A member's delivery channel preference for one notification category."""

    __tablename__ = "notification_settings"

    member_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    category: Mapped[str] = mapped_column(String(32), primary_key=True)
    channel: Mapped[str] = mapped_column(String(16))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
