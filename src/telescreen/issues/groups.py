"""Shared issue group lookups and lifecycle coordination helpers."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import ensure_utc
from telescreen.core.ids import new_id
from telescreen.core.vocabulary import (
    IssueEventKind,
    IssueHistoryKind,
    IssueStatus,
    RuleClass,
    Severity,
)
from telescreen.db.models import FindingRow, IssueHistoryRow, IssueMergeRow, IssueRow
from telescreen.ports.issues import IssueEvent, IssueEventSink


class GroupAction(StrEnum):
    """Supported operations cascaded from a group primary."""

    STATUS = "status"
    ASSIGN = "assign"
    ARCHIVE = "archive"
    UNARCHIVE = "unarchive"
    RESOLVE = "resolve"
    VERIFY = "verify"
    REVIEW = "review"


@dataclass(frozen=True)
class GroupChange:
    """A safe, explicit change to apply to active group children."""

    action: GroupAction
    actor_ref: str
    now: datetime
    status: IssueStatus | None = None
    assignee_member_id: str | None = None
    assignee_team_id: str | None = None
    archive_kind: str | None = None
    archive_until: datetime | None = None
    archive_threshold: int | None = None
    archive_baseline: int | None = None
    archived_at: datetime | None = None


async def active_children(session: AsyncSession, primary_id: str) -> tuple[IssueRow, ...]:
    """Return active merged children in stable issue-number order."""
    rows: tuple[IssueRow, ...] = tuple(
        (
            await session.scalars(
                select(IssueRow)
                .join(IssueMergeRow, IssueMergeRow.merged_issue_id == IssueRow.id)
                .where(
                    IssueMergeRow.primary_issue_id == primary_id,
                    IssueMergeRow.unmerged_at.is_(None),
                    IssueRow.merged_into_id == primary_id,
                )
                .order_by(IssueRow.number)
            )
        ).all()
    )
    return rows


async def primary_of(session: AsyncSession, issue: IssueRow) -> IssueRow:
    """Return the active primary for an issue, or the issue itself."""
    if issue.merged_into_id is None:
        return issue
    primary: IssueRow | None = await session.get(IssueRow, issue.merged_into_id)
    return primary if primary is not None else issue


async def recompute_group_counters(session: AsyncSession, primary: IssueRow) -> None:
    """Recompute counts and observation bounds over a primary and active children."""
    ids: tuple[str, ...] = (primary.id,) + tuple(
        child.id for child in await active_children(session, primary.id)
    )
    rows: tuple[FindingRow, ...] = tuple(
        (await session.scalars(select(FindingRow).where(FindingRow.issue_id.in_(ids)))).all()
    )
    primary.times_seen = len(rows)
    versions: tuple[int, ...] = tuple(row.fingerprint_key_version for row in rows)
    highest: int = max(versions, default=0)
    primary.distinct_fingerprints = len(
        {row.fingerprint for row in rows if row.fingerprint_key_version == highest}
    )
    if rows:
        primary.first_seen = min(ensure_utc(row.observed_at) for row in rows)
        primary.last_seen = max(ensure_utc(row.observed_at) for row in rows)


async def cascade_to_children(
    session: AsyncSession, primary: IssueRow, apply: GroupChange
) -> tuple[str, ...]:
    """Apply one supported lifecycle field change to active children."""
    skipped: list[str] = []
    for child in await active_children(session, primary.id):
        target: IssueStatus | None = _target_status(apply)
        if target is not None and not _can_transition(IssueStatus(child.status), target):
            skipped.append(child.id)
            continue
        if apply.action is GroupAction.STATUS:
            if apply.status is None:
                skipped.append(child.id)
                continue
            if child.status == IssueStatus.ARCHIVED.value:
                from telescreen.issues.archive import clear_archive

                clear_archive(child)
            child.status = apply.status.value
            child.resolved_at = apply.now if apply.status is IssueStatus.RESOLVED else None
        elif apply.action is GroupAction.ASSIGN:
            child.assignee_member_id = apply.assignee_member_id
            child.assignee_team_id = apply.assignee_team_id
        elif apply.action is GroupAction.ARCHIVE:
            child.status = IssueStatus.ARCHIVED.value
            child.archive_kind = apply.archive_kind
            child.archive_until = apply.archive_until
            child.archive_threshold = apply.archive_threshold
            child.archive_baseline = apply.archive_baseline
            child.archived_at = apply.archived_at
        elif apply.action is GroupAction.UNARCHIVE:
            child.status = IssueStatus.UNRESOLVED.value
            from telescreen.issues.archive import clear_archive

            clear_archive(child)
        elif apply.action is GroupAction.RESOLVE:
            if child.status == IssueStatus.ARCHIVED.value:
                from telescreen.issues.archive import clear_archive

                clear_archive(child)
            child.status = IssueStatus.RESOLVED.value
            child.resolved_at = apply.now
        elif apply.action is GroupAction.VERIFY:
            if child.status == IssueStatus.ARCHIVED.value:
                from telescreen.issues.archive import clear_archive

                clear_archive(child)
            child.status = IssueStatus.PENDING_VERIFICATION.value
        else:
            child.needs_review = False
        child.updated_at = apply.now
        child.needs_review = False
        session.add(
            IssueHistoryRow(
                id=new_id(),
                issue_id=child.id,
                kind=_history_kind(apply).value,
                actor_ref=apply.actor_ref[:256],
                note=f"via TELE-{primary.number}",
                at=apply.now,
            )
        )
    return tuple(skipped)


async def propagate_child_open(
    session: AsyncSession,
    child: IssueRow,
    now: datetime,
    events: IssueEventSink,
) -> None:
    """Propagate a reopened child to its primary without escalating archives."""
    primary: IssueRow = await primary_of(session, child)
    if primary.id == child.id:
        return
    previous: IssueStatus = IssueStatus(primary.status)
    target: IssueStatus | None = {
        IssueStatus.RESOLVED: IssueStatus.REGRESSED,
        IssueStatus.PENDING_VERIFICATION: IssueStatus.UNRESOLVED,
        IssueStatus.ARCHIVED: IssueStatus.ESCALATING,
    }.get(previous)
    if target is None:
        return
    primary.status = target.value
    primary.needs_review = True
    primary.updated_at = now
    if previous is IssueStatus.ARCHIVED:
        from telescreen.issues.archive import clear_archive

        clear_archive(primary)
    session.add(
        IssueHistoryRow(
            id=new_id(),
            issue_id=primary.id,
            kind=(
                IssueHistoryKind.REGRESSED.value
                if target is IssueStatus.REGRESSED
                else IssueHistoryKind.ESCALATED.value
                if target is IssueStatus.ESCALATING
                else IssueHistoryKind.STATUS_CHANGED.value
            ),
            actor_ref="system:scanner",
            note=f"via TELE-{child.number}",
            from_value=previous.value,
            to_value=target.value,
            at=now,
        )
    )
    event_kind: IssueEventKind = (
        IssueEventKind.ISSUE_ESCALATING
        if target is IssueStatus.ESCALATING
        else IssueEventKind.ISSUE_REGRESSED
        if target is IssueStatus.REGRESSED
        else IssueEventKind.ISSUE_CREATED
    )
    await events.on_issue_event(
        session,
        IssueEvent(
            event_kind,
            new_id(),
            primary.id,
            primary.source,
            RuleClass(primary.rule_class),
            Severity(primary.severity),
            now,
        ),
    )


def _target_status(change: GroupChange) -> IssueStatus | None:
    """Return the lifecycle target for an operation that changes status."""
    if change.action is GroupAction.STATUS:
        return change.status
    if change.action is GroupAction.ARCHIVE:
        return IssueStatus.ARCHIVED
    if change.action is GroupAction.UNARCHIVE:
        return IssueStatus.UNRESOLVED
    if change.action is GroupAction.RESOLVE:
        return IssueStatus.RESOLVED
    if change.action is GroupAction.VERIFY:
        return IssueStatus.PENDING_VERIFICATION
    return None


def _can_transition(current: IssueStatus, target: IssueStatus) -> bool:
    """Check group lifecycle targets against the issue state machine."""
    if target is IssueStatus.ARCHIVED:
        return current in (
            IssueStatus.UNRESOLVED,
            IssueStatus.ACKNOWLEDGED,
            IssueStatus.IN_PROGRESS,
            IssueStatus.REGRESSED,
            IssueStatus.ESCALATING,
        )
    if target is IssueStatus.RESOLVED:
        return current in (
            IssueStatus.UNRESOLVED,
            IssueStatus.ACKNOWLEDGED,
            IssueStatus.IN_PROGRESS,
            IssueStatus.REGRESSED,
            IssueStatus.ESCALATING,
            IssueStatus.ARCHIVED,
        )
    if target is IssueStatus.PENDING_VERIFICATION:
        return current in (
            IssueStatus.UNRESOLVED,
            IssueStatus.ACKNOWLEDGED,
            IssueStatus.IN_PROGRESS,
            IssueStatus.REGRESSED,
            IssueStatus.ESCALATING,
            IssueStatus.ARCHIVED,
        )
    if target is IssueStatus.UNRESOLVED and current is IssueStatus.ARCHIVED:
        return True
    if target is IssueStatus.UNRESOLVED and current is IssueStatus.ESCALATING:
        return True
    if target is IssueStatus.ACKNOWLEDGED:
        return current in (IssueStatus.UNRESOLVED, IssueStatus.REGRESSED)
    if target is IssueStatus.IN_PROGRESS:
        return current in (
            IssueStatus.UNRESOLVED,
            IssueStatus.ACKNOWLEDGED,
            IssueStatus.REGRESSED,
            IssueStatus.ESCALATING,
        )
    if target is IssueStatus.UNRESOLVED:
        return current in (
            IssueStatus.ACKNOWLEDGED,
            IssueStatus.IN_PROGRESS,
            IssueStatus.REGRESSED,
            IssueStatus.RESOLVED,
            IssueStatus.ESCALATING,
        )
    return False


def _history_kind(change: GroupChange) -> IssueHistoryKind:
    """Return the history kind matching a cascaded operation."""
    if change.action is GroupAction.ARCHIVE:
        return IssueHistoryKind.ARCHIVED
    if change.action is GroupAction.UNARCHIVE:
        return IssueHistoryKind.UNARCHIVED
    if change.action is GroupAction.REVIEW:
        return IssueHistoryKind.REVIEWED
    if change.action is GroupAction.ASSIGN:
        return IssueHistoryKind.ASSIGNED
    if change.action is GroupAction.RESOLVE:
        return IssueHistoryKind.STATUS_CHANGED
    if change.action is GroupAction.VERIFY:
        return IssueHistoryKind.VERIFICATION_REQUESTED
    return IssueHistoryKind.STATUS_CHANGED
