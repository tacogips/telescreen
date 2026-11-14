"""Bounded issue archive conditions and expiry maintenance."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import JsonValue, json_int, json_object
from telescreen.core.vocabulary import (
    ActorKind,
    ArchiveKind,
    ErrorCode,
    IssueEventKind,
    IssueHistoryKind,
    IssueStatus,
    RuleClass,
    Severity,
    Visibility,
    visibility_rank,
)
from telescreen.db.models import FindingRow, IssueHistoryRow, IssueRow
from telescreen.issues.groups import active_children, recompute_group_counters
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.issues import IssueActivity, IssueActivityNotifier, IssueEvent, IssueEventSink
from telescreen.ports.workflow import ScheduleProvider


@dataclass(frozen=True)
class ArchivePolicy:
    """Escalation threshold settings for archived issues."""

    min_findings: int = 10
    factor: int = 3


@dataclass(frozen=True)
class ArchiveRequest:
    """Validated inputs for one archive condition."""

    kind: ArchiveKind
    duration_minutes: int | None = None
    count: int | None = None


class RebaseScope(StrEnum):
    """Finding scope used when a group changes membership."""

    OWN = "own"
    GROUP = "group"


def validate_archive_request(request: ArchiveRequest) -> None:
    """Reject archive conditions with invalid or irrelevant values."""
    valid: bool = True
    if request.kind is ArchiveKind.FOR_DURATION:
        valid = (
            request.duration_minutes is not None
            and 30 <= request.duration_minutes <= 129600
            and request.count is None
        )
    elif request.kind in (ArchiveKind.UNTIL_FINDINGS, ArchiveKind.UNTIL_VALUES):
        valid = (
            request.count is not None
            and 1 <= request.count <= 100000
            and request.duration_minutes is None
        )
    elif request.duration_minutes is not None or request.count is not None:
        valid = False
    if not valid:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid archive condition")


def clear_archive(issue: IssueRow) -> None:
    """Clear all persisted archive metadata when leaving archived status."""
    issue.archive_kind = None
    issue.archive_until = None
    issue.archive_threshold = None
    issue.archive_baseline = None
    issue.archived_at = None


async def archive_issue(
    session: AsyncSession,
    issue: IssueRow,
    request: ArchiveRequest,
    actor_ref: str,
    now: datetime,
    policy: ArchivePolicy,
    audit: AuditLog | None = None,
    activity: IssueActivityNotifier | None = None,
) -> None:
    """Archive an open issue with a bounded condition and audit trail."""
    validate_archive_request(request)
    if issue.status not in tuple(
        status.value
        for status in (
            IssueStatus.UNRESOLVED,
            IssueStatus.ACKNOWLEDGED,
            IssueStatus.IN_PROGRESS,
            IssueStatus.REGRESSED,
            IssueStatus.ESCALATING,
        )
    ):
        raise TelescreenError(ErrorCode.INVALID_TRANSITION, "issue cannot be archived")
    await recompute_group_counters(session, issue)
    issue.status = IssueStatus.ARCHIVED.value
    issue.needs_review = False
    issue.archived_at = now
    issue.archive_kind = request.kind.value
    issue.archive_until = now + timedelta(
        minutes=request.duration_minutes
        if request.kind is ArchiveKind.FOR_DURATION and request.duration_minutes is not None
        else 90 * 24 * 60
    )
    issue.archive_baseline = (
        issue.times_seen
        if request.kind is ArchiveKind.UNTIL_FINDINGS
        else issue.distinct_fingerprints
        if request.kind is ArchiveKind.UNTIL_VALUES
        else None
    )
    issue.archive_threshold = request.count
    if request.kind is ArchiveKind.UNTIL_ESCALATING:
        issue.archive_threshold = await escalation_threshold(session, issue, now, policy)
    issue.updated_at = now
    from telescreen.issues.groups import GroupAction, GroupChange, cascade_to_children

    await cascade_to_children(
        session,
        issue,
        GroupChange(
            GroupAction.ARCHIVE,
            actor_ref,
            now,
            archive_kind=issue.archive_kind,
            archive_until=issue.archive_until,
            archive_threshold=issue.archive_threshold,
            archive_baseline=issue.archive_baseline,
            archived_at=issue.archived_at,
        ),
    )
    _history(session, issue, IssueHistoryKind.ARCHIVED, actor_ref, now)
    if audit is not None:
        await _audit(audit, session, "issue.archived", issue, actor_ref)
    if activity is not None:
        await activity.on_activity(
            session, IssueActivity(IssueHistoryKind.ARCHIVED, issue.id, actor_ref, now)
        )


async def unarchive_issue(
    session: AsyncSession,
    issue: IssueRow,
    actor_ref: str,
    now: datetime,
    audit: AuditLog | None = None,
    activity: IssueActivityNotifier | None = None,
) -> None:
    """Return an archived issue to unresolved and clear archive metadata."""
    if issue.status != IssueStatus.ARCHIVED.value:
        raise TelescreenError(ErrorCode.INVALID_TRANSITION, "issue is not archived")
    issue.status = IssueStatus.UNRESOLVED.value
    issue.needs_review = False
    clear_archive(issue)
    issue.updated_at = now
    from telescreen.issues.groups import GroupAction, GroupChange, cascade_to_children

    await cascade_to_children(session, issue, GroupChange(GroupAction.UNARCHIVE, actor_ref, now))
    _history(session, issue, IssueHistoryKind.UNARCHIVED, actor_ref, now)
    if audit is not None:
        await _audit(audit, session, "issue.unarchived", issue, actor_ref)
    if activity is not None:
        await activity.on_activity(
            session, IssueActivity(IssueHistoryKind.UNARCHIVED, issue.id, actor_ref, now)
        )


async def evaluate_after_ingest(
    session: AsyncSession,
    primary: IssueRow,
    previous_visibility: Visibility,
    now: datetime,
    policy: ArchivePolicy | None = None,
    audit: AuditLog | None = None,
    events: IssueEventSink | None = None,
    activity: IssueActivityNotifier | None = None,
) -> bool:
    """Escalate an archived issue when its condition or exposure changes."""
    if primary.status != IssueStatus.ARCHIVED.value:
        return False
    effective_policy: ArchivePolicy = policy or ArchivePolicy()
    await recompute_group_counters(session, primary)
    new_rank: int | None = visibility_rank(Visibility(primary.exposure_visibility))
    old_rank: int | None = visibility_rank(previous_visibility)
    widened: bool = new_rank is not None and (old_rank is None or new_rank > old_rank)
    kind: ArchiveKind = ArchiveKind(primary.archive_kind or ArchiveKind.FOR_DURATION.value)
    met: bool = False
    if kind is ArchiveKind.UNTIL_FINDINGS:
        met = primary.times_seen - (primary.archive_baseline or 0) >= (
            primary.archive_threshold or 0
        )
    elif kind is ArchiveKind.UNTIL_VALUES:
        met = primary.distinct_fingerprints - (primary.archive_baseline or 0) >= (
            primary.archive_threshold or 0
        )
    elif kind is ArchiveKind.UNTIL_ESCALATING:
        threshold: int = primary.archive_threshold or await escalation_threshold(
            session, primary, now, effective_policy
        )
        rows: tuple[FindingRow, ...] = await _group_findings(session, primary)
        met = (
            sum(1 for row in rows if ensure_utc(row.observed_at) > now - timedelta(hours=24))
            >= threshold
        )
    if not widened and not met:
        return False
    primary.status = IssueStatus.ESCALATING.value
    primary.needs_review = True
    clear_archive(primary)
    primary.updated_at = now
    _history(session, primary, IssueHistoryKind.ESCALATED, "system:scanner", now)
    if audit is not None:
        await _audit(audit, session, "issue.escalated", primary, "system:scanner")
    if events is not None:
        await events.on_issue_event(
            session,
            IssueEvent(
                IssueEventKind.ISSUE_ESCALATING,
                new_id(),
                primary.id,
                primary.source,
                RuleClass(primary.rule_class),
                Severity(primary.severity),
                now,
            ),
        )
    if activity is not None:
        await activity.on_activity(
            session,
            IssueActivity(IssueHistoryKind.ESCALATED, primary.id, "system:scanner", now),
        )
    return True


async def rebase_archive(
    session: AsyncSession,
    issue: IssueRow,
    scope: RebaseScope,
    now: datetime | None = None,
    policy: ArchivePolicy | None = None,
) -> None:
    """Rebase condition counters without evaluating or extending archive time."""
    if issue.status != IssueStatus.ARCHIVED.value or issue.archive_kind is None:
        return
    effective_now: datetime = now or ensure_utc(issue.archived_at or issue.updated_at)
    effective_policy: ArchivePolicy = policy or ArchivePolicy()
    rows: tuple[FindingRow, ...] = await _scope_findings(session, issue, scope)
    kind: ArchiveKind = ArchiveKind(issue.archive_kind)
    if kind is ArchiveKind.UNTIL_FINDINGS:
        issue.archive_baseline = len(rows)
    elif kind is ArchiveKind.UNTIL_VALUES:
        versions: tuple[int, ...] = tuple(row.fingerprint_key_version for row in rows)
        highest_version: int = max(versions, default=0)
        issue.archive_baseline = len(
            {row.fingerprint for row in rows if row.fingerprint_key_version == highest_version}
        )
    elif kind is ArchiveKind.UNTIL_ESCALATING:
        issue.archive_threshold = _rate_threshold(rows, effective_now, effective_policy)


@dataclass
class ArchiveMaintenanceProvider(ScheduleProvider):
    """Expire bounded archives at most once per minute per process."""

    audit: AuditLog
    activity: IssueActivityNotifier
    _last_run: datetime | None = None

    async def tick(self, session: AsyncSession, now: datetime) -> int:
        """Return due archived issues to unresolved and notify activity ports."""
        if self._last_run is not None and now - self._last_run < timedelta(minutes=1):
            return 0
        self._last_run = now
        rows: tuple[IssueRow, ...] = tuple(
            (
                await session.scalars(
                    select(IssueRow)
                    .where(
                        IssueRow.status == IssueStatus.ARCHIVED.value,
                        IssueRow.archive_until <= now,
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        for issue in rows:
            issue.status = IssueStatus.UNRESOLVED.value
            issue.needs_review = True
            issue.updated_at = now
            clear_archive(issue)
            _history(session, issue, IssueHistoryKind.UNARCHIVED, "system:archive", now)
            await _audit(self.audit, session, "issue.unarchived", issue, "system:archive")
            await self.activity.on_activity(
                session,
                IssueActivity(IssueHistoryKind.UNARCHIVED, issue.id, "system:archive", now),
            )
        return len(rows)


async def escalation_threshold(
    session: AsyncSession, issue: IssueRow, now: datetime, policy: ArchivePolicy
) -> int:
    """Compute a bounded threshold from the recent group finding rate."""
    rows: tuple[FindingRow, ...] = await _group_findings(session, issue)
    return _rate_threshold(rows, now, policy)


def _rate_threshold(rows: tuple[FindingRow, ...], now: datetime, policy: ArchivePolicy) -> int:
    """Compute the bounded escalation threshold for findings recent to ``now``."""
    counts: list[tuple[str, int]] = []
    for row in rows:
        observed_at: datetime = ensure_utc(row.observed_at)
        if now - timedelta(days=7) <= observed_at <= now:
            day: str = observed_at.date().isoformat()
            prior_index: int | None = next(
                (index for index, (current_day, _) in enumerate(counts) if current_day == day),
                None,
            )
            if prior_index is None:
                counts.append((day, 1))
            else:
                prior_count: int = counts[prior_index][1]
                counts[prior_index] = (day, prior_count + 1)
    peak: int = max((count for _, count in counts), default=0)
    trailing: int = sum(
        1 for row in rows if now - timedelta(hours=24) < ensure_utc(row.observed_at) <= now
    )
    return max(policy.min_findings, policy.factor * max(peak, trailing))


async def _group_findings(session: AsyncSession, issue: IssueRow) -> tuple[FindingRow, ...]:
    """Return findings belonging to an issue and its active children."""
    ids: tuple[str, ...] = (issue.id,) + tuple(
        child.id for child in await active_children(session, issue.id)
    )
    return tuple(
        (await session.scalars(select(FindingRow).where(FindingRow.issue_id.in_(ids)))).all()
    )


async def _scope_findings(
    session: AsyncSession, issue: IssueRow, scope: RebaseScope
) -> tuple[FindingRow, ...]:
    """Return findings for the chosen rebase scope."""
    if scope is RebaseScope.OWN:
        return tuple(
            (await session.scalars(select(FindingRow).where(FindingRow.issue_id == issue.id))).all()
        )
    return await _group_findings(session, issue)


def _history(
    session: AsyncSession, issue: IssueRow, kind: IssueHistoryKind, actor_ref: str, now: datetime
) -> None:
    """Append a lifecycle history row without free-form content."""
    session.add(
        IssueHistoryRow(
            id=new_id(), issue_id=issue.id, kind=kind.value, actor_ref=actor_ref[:256], at=now
        )
    )


async def _audit(
    audit: AuditLog, session: AsyncSession, action: str, issue: IssueRow, actor_ref: str
) -> None:
    """Write count-only audit details for a lifecycle action."""
    actor_kind: ActorKind = (
        ActorKind.MEMBER if actor_ref.startswith("member:") else ActorKind.SYSTEM
    )
    details: JsonValue = json_object((("changed", json_int(1)),))
    await audit.append(
        session,
        AuditEntry(
            actor_kind,
            actor_ref[:256],
            action,
            "issue",
            issue.id,
            "Issue lifecycle updated",
            details,
        ),
    )
