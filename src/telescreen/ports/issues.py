"""Issue recording, reading, and scan ports."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.vocabulary import (
    DetectorKind,
    ExceptionDuration,
    IssueEventKind,
    IssueHistoryKind,
    IssuePriority,
    IssueStatus,
    RemediationAction,
    RuleClass,
    Severity,
    SuggestionReason,
    Visibility,
)
from telescreen.ports.asset_context import ResourceIssueRef as ResourceIssueRef
from telescreen.ports.collectors import ResourceContent
from telescreen.ports.findings import ExternalScan, RecordOutcome, ResourceScan


@dataclass(frozen=True)
class IssueHistoryEntry:
    """A masked issue history change."""

    issue_id: str
    kind: IssueHistoryKind
    actor_ref: str
    at: datetime
    note: str | None = None
    from_value: str | None = None
    to_value: str | None = None


class IssueRecorder(Protocol):
    """Persist scan findings and issue history in a caller transaction."""

    async def record_scan(self, session: AsyncSession, scan: ResourceScan) -> RecordOutcome:
        """Record a resource scan result."""
        ...

    async def record_external(self, session: AsyncSession, scan: ExternalScan) -> RecordOutcome:
        """Record an external masked scan result."""
        ...

    async def add_history(self, session: AsyncSession, entry: IssueHistoryEntry) -> None:
        """Append one issue history entry."""
        ...

    async def resolve_unchecked(
        self, session: AsyncSession, collector_config_id: str, checked_before: datetime
    ) -> int:
        """Resolve resources not checked since the supplied watermark."""
        ...


class ResourceScanner(Protocol):
    """Scan transient content, mask results, and record them atomically."""

    async def scan_resource(
        self,
        content: ResourceContent,
        scan_job_id: str,
        detectors: tuple[DetectorKind, ...] | None = None,
    ) -> RecordOutcome:
        """Scan one resource using the configured pipeline or explicit detectors."""
        ...


@dataclass(frozen=True)
class IssueSnapshot:
    """Masked issue fields needed by other feature packages."""

    id: str
    number: int
    short_id: str
    display_title: str
    status: IssueStatus
    source: str
    rule_class: RuleClass
    severity: Severity
    display_locator: str
    times_seen: int
    first_seen: datetime
    last_seen: datetime
    collector_config_id: str | None
    ref_json: str | None
    assignee_ref: str | None = None
    risk_score: int = 0
    visibility: Visibility = Visibility.UNKNOWN
    masked_excerpt: str | None = None
    priority: IssuePriority | None = None
    assignee_team_slug: str | None = None


@dataclass(frozen=True)
class FindingsRecorded:
    """Count-only observation that findings were attached to an issue."""

    issue_id: str
    inserted: int
    observed_at: datetime


class FindingsObserver(Protocol):
    """Observe issue finding inserts in the caller transaction."""

    async def on_findings_recorded(self, session: AsyncSession, event: FindingsRecorded) -> None:
        """React to finding inserts without receiving detected content."""
        ...


@dataclass(frozen=True)
class IssueActivity:
    """Masked issue activity data for an outbound notification."""

    kind: IssueHistoryKind
    issue_id: str
    actor_ref: str
    at: datetime
    assignee_member_id: str | None = None
    comment_id: str | None = None
    from_value: str | None = None
    to_value: str | None = None


class IssueActivityNotifier(Protocol):
    """Notify subscribers about persisted issue activity."""

    async def on_activity(self, session: AsyncSession, activity: IssueActivity) -> None:
        """Handle one issue activity event."""
        ...


@dataclass(frozen=True)
class ReportIssue:
    """Masked issue summary included in the weekly report."""

    issue_id: str
    short_id: str
    display_title: str
    findings: int


@dataclass(frozen=True)
class WeeklySummary:
    """Count-only and masked weekly issue summary."""

    start: datetime
    end: datetime
    new_count: int
    resolved_count: int
    regressed_count: int
    escalating_count: int
    open_count: int
    mttr_seconds: int | None
    top_issues: tuple[ReportIssue, ...]


class IssueReportSource(Protocol):
    """Provide masked weekly summaries and member open issue counts."""

    async def weekly_summary(
        self, session: AsyncSession, start: datetime, end: datetime
    ) -> WeeklySummary:
        """Return one weekly summary over a caller-selected range."""
        ...

    async def assigned_open_count(self, session: AsyncSession, member_id: str) -> int:
        """Return the open issue count assigned to a member."""
        ...


@dataclass(frozen=True)
class SuggestedAssignee:
    """A suggested member or team reference and its source."""

    ref: str
    reason: SuggestionReason


class AssigneeSuggester(Protocol):
    """Suggest issue assignees from ownership and policy."""

    async def suggest(self, session: AsyncSession, issue_id: str) -> tuple[SuggestedAssignee, ...]:
        """Return ordered masked assignee references."""
        ...


@dataclass(frozen=True)
class NoopFindingsObserver:
    """Default no-op findings observer."""

    async def on_findings_recorded(self, session: AsyncSession, event: FindingsRecorded) -> None:
        """Accept a findings event without taking action."""
        del session, event


@dataclass(frozen=True)
class NoopIssueActivityNotifier:
    """Default no-op issue activity notifier."""

    async def on_activity(self, session: AsyncSession, activity: IssueActivity) -> None:
        """Accept an activity event without taking action."""
        del session, activity


@dataclass(frozen=True)
class RemediationReceipt:
    """Count-only result of requesting issue verification."""

    issue_ids: tuple[str, ...]
    rescans_enqueued: int


class ResourceIssueReader(Protocol):
    """Read open issue references for a set of keyed resources."""

    async def open_issues_for_resources(
        self, session: AsyncSession, resource_keys: tuple[str, ...]
    ) -> tuple[ResourceIssueRef, ...]:
        """Return open issue refs attached to the supplied resource keys."""
        ...


class IssueRemediation(Protocol):
    """Request verification, exceptions, or team reassignment for an issue."""

    async def request_verification(
        self,
        session: AsyncSession,
        issue_id: str,
        action: RemediationAction,
        include_linked: bool,
        actor_ref: str,
        note: str | None,
    ) -> RemediationReceipt:
        """Queue read-only verification rescans for an issue."""
        ...

    async def request_exception(
        self,
        session: AsyncSession,
        issue_id: str,
        reason: str,
        duration: ExceptionDuration,
        actor_ref: str,
        is_admin: bool,
    ) -> str:
        """Request a time-bounded business exception and return its id."""
        ...

    async def release_to_team(self, session: AsyncSession, issue_id: str, actor_ref: str) -> None:
        """Release an issue from its current member assignee to its team."""
        ...


@dataclass(frozen=True)
class OpenResourceRef:
    """Opaque resource reference for an open issue."""

    issue_id: str
    resource_key: str
    ref_json: str | None
    sweep_covered: bool


class IssueReader(Protocol):
    """Read masked issue snapshots and open resource references."""

    async def get(self, session: AsyncSession, issue_id: str) -> IssueSnapshot | None:
        """Return an issue snapshot or None when it does not exist."""
        ...

    async def open_resource_refs(
        self, session: AsyncSession, collector_config_id: str
    ) -> tuple[OpenResourceRef, ...]:
        """Return open resource references for a collector configuration."""
        ...


@dataclass(frozen=True)
class IssueEvent:
    """An issue lifecycle event delivered to subscribed systems."""

    kind: IssueEventKind
    event_id: str
    issue_id: str
    source: str
    rule_class: RuleClass
    severity: Severity
    at: datetime


class IssueEventSink(Protocol):
    """Receive issue lifecycle events in the caller transaction."""

    async def on_issue_event(self, session: AsyncSession, event: IssueEvent) -> None:
        """Handle one issue event."""
        ...
