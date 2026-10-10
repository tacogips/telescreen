"""Collector configuration, scan job and replay nonce models."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from telescreen.db.base import Base


class CollectorConfigRow(Base, kw_only=True):
    """Configuration for a built-in or custom collector."""

    __tablename__ = "collector_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    trigger_mode: Mapped[str] = mapped_column(String(32))
    cron: Mapped[str | None] = mapped_column(String(128), default=None)
    sweep_cron: Mapped[str | None] = mapped_column(String(128), default=None)
    sweep_max_concurrency: Mapped[int] = mapped_column(Integer, default=16)
    selector_json: Mapped[str] = mapped_column(Text)
    credential_ref: Mapped[str | None] = mapped_column(String(512), default=None)
    cursor_json: Mapped[str | None] = mapped_column(Text, default=None)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ScanJobRow(Base, kw_only=True):
    """A transient scan execution and count-only outcome."""

    __tablename__ = "scan_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    collector_config_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    source: Mapped[str] = mapped_column(String(32))
    trigger: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    resources_scanned: Mapped[int] = mapped_column(Integer, default=0)
    resources_skipped: Mapped[int] = mapped_column(Integer, default=0)
    findings_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)


class ReplayNonceRow(Base, kw_only=True):
    """A scoped nonce used to reject replayed webhooks and callbacks."""

    __tablename__ = "replay_nonces"

    scope: Mapped[str] = mapped_column(String(128), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(256), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ResourceCoverageRow(Base, kw_only=True):
    """Latest non-scanned state for a keyed resource in a collector."""

    __tablename__ = "resource_coverage"
    __table_args__ = (UniqueConstraint("collector_config_id", "resource_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    collector_config_id: Mapped[str] = mapped_column(String(36))
    resource_key: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(String(32), default=None)
    truncated_bytes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    exposure_visibility: Mapped[str] = mapped_column(
        String(32), default="unknown", server_default="unknown"
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ScanCoverageRow(Base, kw_only=True):
    """Count of resources in a scan job outcome category."""

    __tablename__ = "scan_coverage"

    scan_job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    state: Mapped[str] = mapped_column(String(32), primary_key=True)
    reason: Mapped[str] = mapped_column(String(32), primary_key=True, default="", server_default="")
    count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class CollectorHealthRow(Base, kw_only=True):
    """Aggregate health markers for a collector configuration."""

    __tablename__ = "collector_health"

    collector_config_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    watch_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    open_conditions: Mapped[str] = mapped_column(String(256), default="", server_default="")
    alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
