"""Read-only, redacted query tools shared by MCP and agent chat."""

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Select, func, or_, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import InstrumentedAttribute

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.vocabulary import IssueStatus, RunStatus
from telescreen.db.models import (
    AuditRecordRow,
    FindingRow,
    IssueHistoryRow,
    IssueRow,
    WorkflowDefinitionRow,
    WorkflowRunRow,
)
from telescreen.masking.redact import Redactor


@dataclass(frozen=True)
class IssueSummary:
    """Safe summary of one grouped issue."""

    id: str
    number: int
    title: str
    locator: str
    source: str
    rule_class: str
    severity: str
    status: str
    first_seen: datetime
    last_seen: datetime
    times_seen: int


@dataclass(frozen=True)
class IssueList:
    """A bounded list of matching issues."""

    items: tuple[IssueSummary, ...]


@dataclass(frozen=True)
class FindingView:
    """A safe observation shown in an issue detail."""

    id: str
    location: str
    rule_class: str
    detector: str
    confidence: float
    masked_snippet: str
    observed_at: datetime


@dataclass(frozen=True)
class HistoryView:
    """A safe issue lifecycle history entry."""

    kind: str
    actor_ref: str
    from_value: str | None
    to_value: str | None
    note: str | None
    at: datetime


@dataclass(frozen=True)
class IssueDetail:
    """Issue metadata with its latest masked events and history."""

    issue: IssueSummary
    events: tuple[FindingView, ...]
    history: tuple[HistoryView, ...]


@dataclass(frozen=True)
class CountItem:
    """One grouped incident count."""

    key: str
    count: int


@dataclass(frozen=True)
class IncidentCounts:
    """Counts of issues grouped by one approved field."""

    group_by: str
    items: tuple[CountItem, ...]
    total: int


@dataclass(frozen=True)
class RunSummary:
    """Safe summary of a workflow run."""

    id: str
    workflow: str
    status: str
    issue_id: str | None
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True)
class RunList:
    """A bounded list of workflow runs."""

    items: tuple[RunSummary, ...]


@dataclass(frozen=True)
class AuditSummary:
    """Safe searchable audit entry."""

    seq: int
    at: datetime
    action: str
    target_kind: str
    target_id: str
    actor_ref: str
    summary: str


@dataclass(frozen=True)
class AuditList:
    """A bounded list of matching audit entries."""

    items: tuple[AuditSummary, ...]


class GroupBy(StrEnum):
    """Approved incident grouping columns."""

    STATUS = "status"
    SOURCE = "source"
    RULE_CLASS = "rule_class"
    SEVERITY = "severity"


type AssistantResult = IssueList | IssueDetail | IncidentCounts | RunList | AuditList


@dataclass(frozen=True)
class AssistantTools:
    """Run masked, read-only database queries for assistant consumers."""

    sessions: async_sessionmaker[AsyncSession]
    redactor: Redactor
    clock: Clock

    async def search_issues(self, query: str, status: IssueStatus | None, limit: int) -> IssueList:
        """Search issue title and locator with an optional lifecycle filter."""
        safe_query: str = self._redact(query)
        statement: Select[tuple[IssueRow]] = (
            select(IssueRow).order_by(IssueRow.last_seen.desc()).limit(_bounded(limit))
        )
        if status is not None:
            statement = statement.where(IssueRow.status == status.value)
        if safe_query:
            pattern: str = f"%{safe_query}%"
            statement = statement.where(
                or_(IssueRow.display_title.like(pattern), IssueRow.display_locator.like(pattern))
            )
        async with self.sessions() as session:
            rows: Sequence[IssueRow] = (await session.scalars(statement)).all()
        return IssueList(tuple(self._issue_summary(row) for row in rows))

    async def get_issue(self, issue_id: str) -> IssueDetail | None:
        """Read one issue, its latest ten finding events, and its history."""
        safe_issue_id: str = self._redact(issue_id)
        async with self.sessions() as session:
            issue: IssueRow | None = await session.get(IssueRow, safe_issue_id)
            if issue is None:
                return None
            findings: Sequence[FindingRow] = (
                await session.scalars(
                    select(FindingRow)
                    .where(FindingRow.issue_id == issue.id)
                    .order_by(FindingRow.observed_at.desc())
                    .limit(10)
                )
            ).all()
            history: Sequence[IssueHistoryRow] = (
                await session.scalars(
                    select(IssueHistoryRow)
                    .where(IssueHistoryRow.issue_id == issue.id)
                    .order_by(IssueHistoryRow.at.desc())
                )
            ).all()
        events: tuple[FindingView, ...] = tuple(
            FindingView(
                self._redact(row.id),
                self._redact(row.location),
                self._redact(row.rule_class),
                self._redact(row.detector),
                row.confidence,
                self._redact(row.masked_snippet),
                ensure_utc(row.observed_at),
            )
            for row in findings
        )
        entries: tuple[HistoryView, ...] = tuple(
            HistoryView(
                self._redact(row.kind),
                self._redact(row.actor_ref),
                self._optional_redact(row.from_value),
                self._optional_redact(row.to_value),
                self._optional_redact(row.note),
                ensure_utc(row.at),
            )
            for row in history
        )
        return IssueDetail(self._issue_summary(issue), events, entries)

    async def count_incidents(
        self, start: datetime, end: datetime, group_by: GroupBy
    ) -> IncidentCounts:
        """Count issues first seen in a half-open UTC interval."""
        start_utc: datetime = ensure_utc(start)
        end_utc: datetime = ensure_utc(end)
        if end_utc <= start_utc:
            return IncidentCounts(self._redact(group_by), (), 0)
        column: InstrumentedAttribute[str] = _group_column(group_by)
        async with self.sessions() as session:
            grouped: Sequence[Row[tuple[str, int]]] = (
                await session.execute(
                    select(column, func.count(IssueRow.id))
                    .where(IssueRow.first_seen >= start_utc, IssueRow.first_seen < end_utc)
                    .group_by(column)
                    .order_by(column)
                )
            ).all()
        items: tuple[CountItem, ...] = tuple(
            CountItem(self._redact(key or "unknown"), count) for key, count in grouped
        )
        return IncidentCounts(self._redact(group_by), items, sum(item.count for item in items))

    async def incident_report(self, start: datetime, end: datetime) -> str:
        """Render a masked markdown summary for the requested UTC interval."""
        start_utc: datetime = ensure_utc(start)
        end_utc: datetime = ensure_utc(end)
        severity: IncidentCounts = await self.count_incidents(start_utc, end_utc, GroupBy.SEVERITY)
        rules: IncidentCounts = await self.count_incidents(start_utc, end_utc, GroupBy.RULE_CLASS)
        async with self.sessions() as session:
            resources: Sequence[Row[tuple[str, int]]] = (
                await session.execute(
                    select(IssueRow.display_locator, IssueRow.times_seen)
                    .where(IssueRow.first_seen >= start_utc, IssueRow.first_seen < end_utc)
                    .order_by(IssueRow.times_seen.desc())
                    .limit(10)
                )
            ).all()
            open_count: int = int(
                await session.scalar(
                    select(func.count(IssueRow.id)).where(
                        IssueRow.first_seen >= start_utc,
                        IssueRow.first_seen < end_utc,
                        IssueRow.status.in_(
                            (
                                IssueStatus.UNRESOLVED.value,
                                IssueStatus.ACKNOWLEDGED.value,
                                IssueStatus.IN_PROGRESS.value,
                                IssueStatus.REGRESSED.value,
                            )
                        ),
                    )
                )
                or 0
            )
        resolved_count: int = severity.total - open_count
        lines: list[str] = [
            f"# Incident report: {start_utc.isoformat()} to {end_utc.isoformat()}",
            f"Total incidents: {severity.total}",
            f"Resolved or ignored: {max(0, resolved_count)}; open: {open_count}",
            "",
            "## By severity",
            "| Severity | Count |",
            "| --- | ---: |",
        ]
        lines.extend(f"| {item.key} | {item.count} |" for item in severity.items)
        lines.extend(["", "## By rule class", "| Rule class | Count |", "| --- | ---: |"])
        lines.extend(f"| {item.key} | {item.count} |" for item in rules.items)
        lines.extend(
            ["", "## Top resources by observed events", "| Resource | Events |", "| --- | ---: |"]
        )
        lines.extend(f"| {self._redact(locator)} | {count} |" for locator, count in resources)
        return self._redact("\n".join(lines))

    async def list_workflow_runs(self, status: RunStatus | None, limit: int) -> RunList:
        """List recent workflow runs with their definition names."""
        statement: Select[tuple[WorkflowRunRow, str]] = (
            select(WorkflowRunRow, WorkflowDefinitionRow.name)
            .join(WorkflowDefinitionRow, WorkflowRunRow.definition_id == WorkflowDefinitionRow.id)
            .order_by(WorkflowRunRow.started_at.desc())
            .limit(_bounded(limit))
        )
        if status is not None:
            statement = statement.where(WorkflowRunRow.status == status.value)
        async with self.sessions() as session:
            rows: Sequence[Row[tuple[WorkflowRunRow, str]]] = (
                await session.execute(statement)
            ).all()
        return RunList(
            tuple(
                RunSummary(
                    self._redact(row.id),
                    self._redact(name),
                    self._redact(row.status),
                    self._optional_redact(row.issue_id),
                    ensure_utc(row.started_at),
                    ensure_utc(row.finished_at) if row.finished_at is not None else None,
                )
                for row, name in rows
            )
        )

    async def audit_search(self, query: str, limit: int) -> AuditList:
        """Search audit actions and summaries without returning raw matches."""
        safe_query: str = self._redact(query)
        statement: Select[tuple[AuditRecordRow]] = (
            select(AuditRecordRow).order_by(AuditRecordRow.seq.desc()).limit(_bounded(limit))
        )
        if safe_query:
            pattern: str = f"%{safe_query}%"
            statement = statement.where(
                or_(AuditRecordRow.action.like(pattern), AuditRecordRow.summary.like(pattern))
            )
        async with self.sessions() as session:
            rows: Sequence[AuditRecordRow] = (await session.scalars(statement)).all()
        return AuditList(
            tuple(
                AuditSummary(
                    row.seq,
                    ensure_utc(row.at),
                    self._redact(row.action),
                    self._redact(row.target_kind),
                    self._redact(row.target_id),
                    self._redact(row.actor_ref),
                    self._redact(row.summary),
                )
                for row in rows
            )
        )

    def render_json(self, result: AssistantResult) -> str:
        """Serialize a dataclass result in deterministic JSON form."""
        return json.dumps(asdict(result), default=_json_default, sort_keys=True)

    def _issue_summary(self, row: IssueRow) -> IssueSummary:
        """Convert an ORM issue row to a fully redacted summary."""
        return IssueSummary(
            self._redact(row.id),
            row.number,
            self._redact(row.display_title),
            self._redact(row.display_locator),
            self._redact(row.source),
            self._redact(row.rule_class),
            self._redact(row.severity),
            self._redact(row.status),
            ensure_utc(row.first_seen),
            ensure_utc(row.last_seen),
            row.times_seen,
        )

    def _redact(self, text: str) -> str:
        """Return text after applying the final rule-based leak guard."""
        return self.redactor.redact(text).text

    def _optional_redact(self, text: str | None) -> str | None:
        """Redact an optional string field."""
        return self._redact(text) if text is not None else None


def _bounded(limit: int) -> int:
    """Clamp result limits to the supported 1 through 50 range."""
    return min(max(limit, 1), 50)


def _group_column(group_by: GroupBy) -> InstrumentedAttribute[str]:
    """Return one of the explicitly permitted grouping columns."""
    if group_by is GroupBy.STATUS:
        return IssueRow.status
    if group_by is GroupBy.SOURCE:
        return IssueRow.source
    if group_by is GroupBy.RULE_CLASS:
        return IssueRow.rule_class
    return IssueRow.severity


def _json_default(value: datetime) -> str:
    """Render datetime values as ISO-8601 text for JSON output."""
    return ensure_utc(value).isoformat()
