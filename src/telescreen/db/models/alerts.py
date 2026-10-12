"""Alert rule and fire history database models."""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from telescreen.db.base import Base


class AlertRuleRow(Base, kw_only=True):
    """A configurable alert predicate, action set and firing interval."""

    __tablename__ = "alert_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    enabled: Mapped[bool] = mapped_column()
    trigger_kind: Mapped[str] = mapped_column(String(32))
    threshold: Mapped[int | None] = mapped_column(Integer, default=None)
    window_minutes: Mapped[int | None] = mapped_column(Integer, default=None)
    filters_json: Mapped[str] = mapped_column(Text)
    actions_json: Mapped[str] = mapped_column(Text)
    action_interval_minutes: Mapped[int] = mapped_column(Integer)
    muted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_by: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AlertFireRow(Base, kw_only=True):
    """The outcome and notification count for an evaluated alert rule."""

    __tablename__ = "alert_fires"
    __table_args__ = (Index("ix_alert_fires_rule_issue_at", "rule_id", "issue_id", "fired_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(36))
    issue_id: Mapped[str] = mapped_column(String(36))
    trigger_kind: Mapped[str] = mapped_column(String(32))
    outcome: Mapped[str] = mapped_column(String(32))
    notification_count: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[str | None] = mapped_column(String(36), default=None)
    error_code: Mapped[str | None] = mapped_column(String(32), default=None)
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
