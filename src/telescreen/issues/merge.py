"""Transactional merge and unmerge operations for issue groups."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import json_array, json_int, json_object, json_str
from telescreen.core.vocabulary import (
    UNRESOLVED_STATUSES,
    ActorKind,
    ErrorCode,
    IssueHistoryKind,
    IssueStatus,
)
from telescreen.db.models import IssueExceptionRow, IssueHistoryRow, IssueMergeRow, IssueRow
from telescreen.issues.archive import RebaseScope, rebase_archive
from telescreen.issues.groups import active_children, recompute_group_counters
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.issues import IssueActivity, IssueActivityNotifier
from telescreen.ports.search import SearchIndexer

_MAX_CHILDREN: int = 50


@dataclass
class MergeService:
    """Merge issues and preserve lifecycle and archive invariants."""

    sessions: async_sessionmaker[AsyncSession]
    audit: AuditLog
    clock: Clock
    indexer: SearchIndexer
    activity: IssueActivityNotifier | None

    async def merge(self, primary_id: str, ids: tuple[str, ...], actor: str) -> IssueRow:
        """Merge selected issues under the selected primary in one transaction."""
        now: datetime = ensure_utc(self.clock.now())
        async with self.sessions.begin() as session:
            primary: IssueRow = await self._required(session, primary_id)
            child_ids: tuple[str, ...] = tuple(
                dict.fromkeys(value for value in ids if value != primary_id)
            )
            if not child_ids:
                raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid merge selection")
            children: list[IssueRow] = []
            for issue_id in child_ids:
                children.append(await self._required(session, issue_id))
            descendants: list[IssueRow] = []
            for child in children:
                descendants.extend(await active_children(session, child.id))
            members: tuple[IssueRow, ...] = (primary, *children, *descendants)
            if any(
                issue.status not in tuple(status.value for status in UNRESOLVED_STATUSES)
                and issue.status != IssueStatus.ARCHIVED.value
                for issue in members
            ):
                raise TelescreenError(ErrorCode.INVALID_TRANSITION, "issue cannot be merged")
            if await self._has_active_exception(session, tuple(issue.id for issue in members)):
                raise TelescreenError(
                    ErrorCode.MERGED_GROUP, "issue with exception cannot be merged"
                )
            if primary.merged_into_id is not None:
                raise TelescreenError(ErrorCode.MERGED_GROUP, "issue is already merged")
            existing_children: tuple[IssueRow, ...] = await active_children(session, primary.id)
            projected_child_ids: set[str] = {child.id for child in existing_children}
            for child in children:
                projected_child_ids.add(child.id)
                projected_child_ids.update(
                    item.id for item in await active_children(session, child.id)
                )
            if len(projected_child_ids) > _MAX_CHILDREN:
                raise TelescreenError(ErrorCode.LIMIT_EXCEEDED, "issue group limit reached")
            for child in children:
                child_descendants: tuple[IssueRow, ...] = await active_children(session, child.id)
                for descendant in child_descendants:
                    await self._close_active_merge(session, descendant.id, actor, now)
                    await self._attach(session, primary, descendant, actor, now)
                await self._close_active_merge(session, child.id, actor, now)
                await self._attach(session, primary, child, actor, now)
            await recompute_group_counters(session, primary)
            if primary.status == IssueStatus.ARCHIVED.value:
                await rebase_archive(session, primary, RebaseScope.GROUP, now)
            await self._audit(session, primary, child_ids, actor, "merged")
        await self.indexer.index_issue(primary_id)
        for issue_id in child_ids:
            await self.indexer.index_issue(issue_id)
        return primary

    async def unmerge(self, primary_id: str, ids: tuple[str, ...], actor: str) -> IssueRow:
        """Unmerge selected active children in one transaction."""
        now: datetime = ensure_utc(self.clock.now())
        async with self.sessions.begin() as session:
            primary: IssueRow = await self._required(session, primary_id)
            children: list[IssueRow] = []
            for issue_id in ids:
                child: IssueRow | None = await session.scalar(
                    select(IssueRow).where(
                        IssueRow.id == issue_id, IssueRow.merged_into_id == primary_id
                    )
                )
                if child is None:
                    raise TelescreenError(ErrorCode.VALIDATION_ERROR, "issue is not a group child")
                children.append(child)
            if not children:
                children.extend(await active_children(session, primary_id))
            for child in children:
                merge: IssueMergeRow | None = await session.scalar(
                    select(IssueMergeRow).where(
                        IssueMergeRow.primary_issue_id == primary_id,
                        IssueMergeRow.merged_issue_id == child.id,
                        IssueMergeRow.unmerged_at.is_(None),
                    )
                )
                if merge is None:
                    raise TelescreenError(ErrorCode.VALIDATION_ERROR, "issue is not a group child")
                merge.unmerged_at = now
                merge.unmerged_by = actor[:256]
                child.merged_into_id = None
                child.needs_review = True
                child.updated_at = now
                self._history(session, child, IssueHistoryKind.UNMERGED, actor, now, primary)
                if child.status == IssueStatus.ARCHIVED.value:
                    await rebase_archive(session, child, RebaseScope.OWN, now)
                if self.activity is not None:
                    await self.activity.on_activity(
                        session, IssueActivity(IssueHistoryKind.UNMERGED, child.id, actor, now)
                    )
            await recompute_group_counters(session, primary)
            if primary.status == IssueStatus.ARCHIVED.value:
                await rebase_archive(session, primary, RebaseScope.GROUP, now)
            for child in children:
                self._history(session, primary, IssueHistoryKind.UNMERGED, actor, now, child)
            await self._audit(
                session, primary, tuple(child.id for child in children), actor, "unmerged"
            )
        await self.indexer.index_issue(primary_id)
        for child in children:
            await self.indexer.index_issue(child.id)
        return primary

    async def _required(self, session: AsyncSession, issue_id: str) -> IssueRow:
        """Load an issue or raise a non-sensitive not-found error."""
        issue: IssueRow | None = await session.get(IssueRow, issue_id)
        if issue is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
        return issue

    async def _has_active_exception(self, session: AsyncSession, ids: tuple[str, ...]) -> bool:
        """Check requested and active exception rows for any merge member."""
        return (
            await session.scalar(
                select(IssueExceptionRow.id)
                .where(
                    IssueExceptionRow.issue_id.in_(ids),
                    IssueExceptionRow.status.in_(("requested", "active")),
                )
                .limit(1)
            )
        ) is not None

    async def _close_active_merge(
        self, session: AsyncSession, child_id: str, actor: str, now: datetime
    ) -> None:
        """Close a child's prior parent link before reparenting."""
        prior: IssueMergeRow | None = await session.scalar(
            select(IssueMergeRow).where(
                IssueMergeRow.merged_issue_id == child_id,
                IssueMergeRow.unmerged_at.is_(None),
            )
        )
        if prior is not None:
            prior.unmerged_at = now
            prior.unmerged_by = actor[:256]

    async def _attach(
        self,
        session: AsyncSession,
        primary: IssueRow,
        child: IssueRow,
        actor: str,
        now: datetime,
    ) -> None:
        """Attach a child and record its lifecycle snapshot and history."""
        child.merged_into_id = primary.id
        child.status = primary.status
        child.archive_kind = primary.archive_kind
        child.archive_until = primary.archive_until
        child.archive_threshold = primary.archive_threshold
        child.archive_baseline = primary.archive_baseline
        child.archived_at = primary.archived_at
        child.assignee_member_id = primary.assignee_member_id
        child.assignee_team_id = primary.assignee_team_id
        child.updated_at = now
        session.add(
            IssueMergeRow(
                id=new_id(),
                primary_issue_id=primary.id,
                merged_issue_id=child.id,
                merged_by=actor[:256],
                merged_at=now,
            )
        )
        self._history(session, child, IssueHistoryKind.MERGED, actor, now, primary)
        self._history(session, primary, IssueHistoryKind.MERGED, actor, now, child)
        if self.activity is not None:
            await self.activity.on_activity(
                session, IssueActivity(IssueHistoryKind.MERGED, child.id, actor, now)
            )

    def _history(
        self,
        session: AsyncSession,
        issue: IssueRow,
        kind: IssueHistoryKind,
        actor: str,
        now: datetime,
        related: IssueRow,
    ) -> None:
        """Record a safe group-history link by short issue number."""
        session.add(
            IssueHistoryRow(
                id=new_id(),
                issue_id=issue.id,
                kind=kind.value,
                actor_ref=actor[:256],
                note=f"via TELE-{related.number}",
                at=now,
            )
        )

    async def _audit(
        self,
        session: AsyncSession,
        primary: IssueRow,
        ids: tuple[str, ...],
        actor: str,
        action: str,
    ) -> None:
        """Append an audit record containing issue ids and counts only."""
        await self.audit.append(
            session,
            AuditEntry(
                ActorKind.MEMBER
                if actor.startswith("member:")
                else ActorKind.TOKEN
                if actor.startswith("token:")
                else ActorKind.SYSTEM,
                actor[:256],
                f"issue.{action}",
                "issue",
                primary.id,
                f"Issue {action}",
                json_object(
                    (
                        ("ids", json_array(tuple(json_str(value) for value in ids))),
                        ("count", json_int(len(ids))),
                    )
                ),
            ),
        )
