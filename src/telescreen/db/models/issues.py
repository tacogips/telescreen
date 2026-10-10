"""Issue, finding event and issue history database models."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from telescreen.db.base import Base


class IssueRow(Base, kw_only=True):
    """A grouped sensitive-data finding with lifecycle state."""

    __tablename__ = "issues"
    __table_args__ = (
        Index("ix_issues_sweep_lookup", "collector_config_id", "status", "last_checked_at"),
        Index("ix_issues_status_last_seen", "status", "last_seen"),
        Index("ix_issues_status_risk", "status", "risk_score"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    number: Mapped[int] = mapped_column(Integer, unique=True)
    grouping_key: Mapped[str] = mapped_column(String(64), unique=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    resource_key: Mapped[str] = mapped_column(String(64), index=True)
    display_locator: Mapped[str] = mapped_column(Text)
    resource_ref_json: Mapped[str | None] = mapped_column(Text, default=None)
    collector_config_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    display_title: Mapped[str] = mapped_column(String(512))
    rule_class: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    assignee_member_id: Mapped[str | None] = mapped_column(String(36), default=None)
    assignee_team_id: Mapped[str | None] = mapped_column(String(36), default=None)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sweep_covered: Mapped[bool] = mapped_column(Boolean, default=False)
    times_seen: Mapped[int] = mapped_column(Integer, default=1)
    distinct_fingerprints: Mapped[int] = mapped_column(Integer, default=1)
    regression_count: Mapped[int] = mapped_column(Integer, default=0)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    resolution: Mapped[str | None] = mapped_column(String(32), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    base_severity: Mapped[str] = mapped_column(
        String(32), default="medium", server_default="medium"
    )
    exposure_visibility: Mapped[str] = mapped_column(
        String(32), default="unknown", server_default="unknown", index=True
    )
    exposure_json: Mapped[str | None] = mapped_column(Text, default=None)
    exposure_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    placement_keys_json: Mapped[str | None] = mapped_column(Text, default=None)
    resource_modified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    owner_ref: Mapped[str | None] = mapped_column(String(80), default=None)
    policy_name: Mapped[str | None] = mapped_column(String(64), default=None)
    policy_version: Mapped[int | None] = mapped_column(Integer, default=None)
    risk_score: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    asset_tier: Mapped[str | None] = mapped_column(String(32), default=None)


class FindingRow(Base, kw_only=True):
    """One masked observation of a sensitive-data rule match."""

    __tablename__ = "findings"
    __table_args__ = (
        UniqueConstraint("issue_id", "scan_job_id", "fingerprint", name="uq_findings_observation"),
        Index("ix_findings_key_fingerprint", "fingerprint_key_version", "fingerprint"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    issue_id: Mapped[str] = mapped_column(String(36), index=True)
    scan_job_id: Mapped[str | None] = mapped_column(String(36), default=None)
    source: Mapped[str] = mapped_column(String(32))
    resource_key: Mapped[str] = mapped_column(String(64))
    display_locator: Mapped[str] = mapped_column(Text)
    location: Mapped[str] = mapped_column(Text)
    rule_id: Mapped[str] = mapped_column(String(128))
    rule_class: Mapped[str] = mapped_column(String(32))
    detector: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float)
    masked_snippet: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))
    fingerprint_key_id: Mapped[str] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fingerprint_key_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class PlacementPolicyRow(Base, kw_only=True):
    """Versioned placement policy source and compiled configuration."""

    __tablename__ = "placement_policies"
    __table_args__ = (UniqueConstraint("name", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    source_format: Mapped[str] = mapped_column(String(16))
    source_text: Mapped[str] = mapped_column(Text)
    compiled_json: Mapped[str] = mapped_column(Text)
    effect: Mapped[str] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(Boolean)
    active: Mapped[bool] = mapped_column(Boolean)
    owner_team: Mapped[str | None] = mapped_column(String(128), default=None)
    created_by: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IssueExceptionRow(Base, kw_only=True):
    """A requested or approved time-bounded exception for an issue."""

    __tablename__ = "issue_exceptions"
    __table_args__ = (
        Index("ix_issue_exceptions_issue", "issue_id"),
        Index("ix_issue_exceptions_status_expiry", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    issue_id: Mapped[str] = mapped_column(String(36))
    reason: Mapped[str] = mapped_column(Text)
    duration: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    requested_by: Mapped[str] = mapped_column(String(256))
    approver_ref: Mapped[str | None] = mapped_column(String(256), default=None)
    decided_by: Mapped[str | None] = mapped_column(String(256), default=None)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IssueHistoryRow(Base, kw_only=True):
    """A masked event in an issue's audit history."""

    __tablename__ = "issue_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    issue_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    actor_ref: Mapped[str] = mapped_column(String(256))
    from_value: Mapped[str | None] = mapped_column(Text, default=None)
    to_value: Mapped[str | None] = mapped_column(Text, default=None)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
