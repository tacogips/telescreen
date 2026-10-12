"""Validated, bounded bulk issue actions."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import json_int, json_object, json_str
from telescreen.core.vocabulary import (
    ActorKind,
    ArchiveKind,
    ErrorCode,
    IssueHistoryKind,
    IssuePriority,
    IssueResolution,
    IssueStatus,
    RemediationAction,
)
from telescreen.db.models import IssueRow
from telescreen.issues.archive import (
    ArchivePolicy,
    ArchiveRequest,
    archive_issue,
    unarchive_issue,
    validate_archive_request,
)
from telescreen.issues.groups import GroupAction, GroupChange, cascade_to_children
from telescreen.issues.lifecycle import IssueRemediationService
from telescreen.issues.query.filters import QueryContext
from telescreen.issues.query.model import QueryError
from telescreen.issues.query.period import Period
from telescreen.issues.query.select import MatchResult, select_matching_ids
from telescreen.issues.service import IssueService
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.search import SearchIndexer


class BulkAction(StrEnum):
    """Supported stream bulk actions."""

    STATUS = "status"
    ASSIGN = "assign"
    PRIORITY = "priority"
    ARCHIVE = "archive"
    UNARCHIVE = "unarchive"
    RESOLVE = "resolve"
    MARK_REVIEWED = "mark_reviewed"
    REQUEST_VERIFICATION = "request_verification"


@dataclass(frozen=True)
class BulkArchiveParams:
    """Archive action parameters."""

    kind: ArchiveKind
    duration_minutes: int | None = None
    count: int | None = None


@dataclass(frozen=True)
class BulkVerificationParams:
    """Verification action parameters."""

    action: RemediationAction
    include_linked: bool


@dataclass(frozen=True)
class BulkParams:
    """Optional parameters for a single bulk action."""

    status: IssueStatus | None = None
    assignee_ref: str | None = None
    priority: IssuePriority | None = None
    archive: BulkArchiveParams | None = None
    reason: IssueResolution | None = None
    note: str | None = None
    verification: BulkVerificationParams | None = None


@dataclass(frozen=True)
class BulkRequest:
    """A bulk request after period parsing and structural validation."""

    ids: tuple[str, ...] | None
    query: str | None
    period: Period | None
    action: BulkAction
    params: BulkParams


@dataclass(frozen=True)
class BulkFailure:
    """A failed issue id and a stable error code."""

    issue_id: str
    code: ErrorCode


@dataclass(frozen=True)
class BulkResult:
    """Counts and per-issue outcomes from a bounded bulk request."""

    matched: int
    processed: int
    truncated: bool
    succeeded: tuple[str, ...]
    failed: tuple[BulkFailure, ...]


def validate_bulk_params(action: BulkAction, params: BulkParams) -> None:
    """Reject incomplete or inconsistent action parameters before work begins."""
    valid: bool = True
    if action is BulkAction.STATUS:
        valid = params.status is not None
    elif action is BulkAction.PRIORITY:
        valid = params.priority is not None
    elif action is BulkAction.ARCHIVE:
        valid = params.archive is not None
        if params.archive is not None:
            validate_archive_request(
                ArchiveRequest(
                    params.archive.kind,
                    params.archive.duration_minutes,
                    params.archive.count,
                )
            )
    elif action is BulkAction.RESOLVE:
        valid = params.reason is not None and params.note is not None and bool(params.note.strip())
    elif action is BulkAction.REQUEST_VERIFICATION:
        valid = params.verification is not None
    if not valid:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid bulk parameters")


@dataclass
class BulkService:
    """Apply each selected issue action in a separate database transaction."""

    sessions: async_sessionmaker[AsyncSession]
    issues: IssueService
    remediation: IssueRemediationService
    archive_policy: ArchivePolicy
    clock: Clock
    audit: AuditLog
    indexer: SearchIndexer

    async def run(
        self,
        request: BulkRequest,
        principal_ref: str,
        is_admin: bool,
        query_ctx: QueryContext,
    ) -> BulkResult:
        """Run a validated request and record a count-only summary audit."""
        validate_bulk_params(request.action, request.params)
        if (request.ids is None) == (request.query is None):
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid bulk selection")
        if request.ids is not None and (
            not 1 <= len(request.ids) <= 1000 or len(set(request.ids)) != len(request.ids)
        ):
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid bulk selection")
        if (
            request.action is BulkAction.RESOLVE
            and request.params.reason is IssueResolution.MANUAL_UNVERIFIED
            and not is_admin
        ):
            raise TelescreenError(ErrorCode.FORBIDDEN, "administrator permission required")
        if request.ids is not None:
            identifiers: tuple[str, ...] = request.ids
            matched: int = len(identifiers)
        else:
            if request.query is None or request.period is None:
                raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid bulk selection")
            try:
                async with self.sessions() as session:
                    selection: MatchResult = await select_matching_ids(
                        session, request.query, request.period, query_ctx, 1000
                    )
            except QueryError as error:
                raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid bulk query") from error
            identifiers = selection.ids
            matched = selection.matched
        succeeded: list[str] = []
        failed: list[BulkFailure] = []
        for issue_id in identifiers:
            try:
                async with self.sessions.begin() as session:
                    await self._apply(session, issue_id, request, principal_ref, is_admin)
                succeeded.append(issue_id)
            except TelescreenError as error:
                failed.append(BulkFailure(issue_id, error.code))
            if issue_id in succeeded:
                await self.indexer.index_issue(issue_id)
        processed: int = len(succeeded) + len(failed)
        async with self.sessions.begin() as session:
            await self.audit.append(
                session,
                AuditEntry(
                    ActorKind.MEMBER
                    if principal_ref.startswith("member:")
                    else ActorKind.TOKEN
                    if principal_ref.startswith("token:")
                    else ActorKind.SYSTEM,
                    principal_ref[:256],
                    "issue.bulk",
                    "issue",
                    "bulk",
                    "Bulk issue action completed",
                    json_object(
                        (
                            ("action", json_str(request.action.value)),
                            ("matched", json_int(matched)),
                            ("processed", json_int(processed)),
                            ("succeeded", json_int(len(succeeded))),
                            ("failed", json_int(len(failed))),
                        )
                    ),
                ),
            )
        return BulkResult(matched, processed, matched > processed, tuple(succeeded), tuple(failed))

    async def _apply(
        self,
        session: AsyncSession,
        issue_id: str,
        request: BulkRequest,
        principal_ref: str,
        is_admin: bool,
    ) -> None:
        """Apply one action through the accepted single-issue services."""
        action: BulkAction = request.action
        params: BulkParams = request.params
        if action is BulkAction.STATUS and params.status is not None:
            await self.issues.change_status(session, issue_id, params.status, principal_ref)
        elif action is BulkAction.ASSIGN:
            if params.assignee_ref is None:
                await self._unassign(session, issue_id, principal_ref)
            else:
                await self.issues.assign(session, issue_id, params.assignee_ref, principal_ref)
        elif action is BulkAction.PRIORITY and params.priority is not None:
            await self.issues.set_priority(session, issue_id, params.priority, principal_ref)
        elif action is BulkAction.ARCHIVE and params.archive is not None:
            issue: IssueRow | None = await self.issues.repository.by_id(session, issue_id)
            if issue is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
            archive: BulkArchiveParams = params.archive
            await archive_issue(
                session,
                issue,
                ArchiveRequest(archive.kind, archive.duration_minutes, archive.count),
                principal_ref,
                ensure_utc(self.clock.now()),
                self.archive_policy,
                self.issues.audit,
                self.issues.activity,
            )
        elif action is BulkAction.UNARCHIVE:
            await unarchive_issue(
                session,
                await self._issue(session, issue_id),
                principal_ref,
                ensure_utc(self.clock.now()),
                self.issues.audit,
                self.issues.activity,
            )
        elif action is BulkAction.RESOLVE and params.reason is not None and params.note is not None:
            await self.remediation.resolve_with_reason(
                session, issue_id, params.reason, params.note, principal_ref, is_admin
            )
        elif action is BulkAction.MARK_REVIEWED:
            issue: IssueRow = await self.issues.mark_reviewed(session, issue_id, principal_ref)
            await cascade_to_children(
                session,
                issue,
                GroupChange(GroupAction.REVIEW, principal_ref, ensure_utc(self.clock.now())),
            )
        elif action is BulkAction.REQUEST_VERIFICATION and params.verification is not None:
            await self.remediation.request_verification(
                session,
                issue_id,
                params.verification.action,
                params.verification.include_linked,
                principal_ref,
                None,
            )
        else:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid bulk parameters")

    async def _issue(self, session: AsyncSession, issue_id: str) -> IssueRow:
        """Return an issue or a safe not-found error."""
        issue: IssueRow | None = await self.issues.repository.by_id(session, issue_id)
        if issue is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
        return issue

    async def _unassign(self, session: AsyncSession, issue_id: str, actor: str) -> None:
        """Clear assignment with the same history, audit and group cascade as assign."""
        issue: IssueRow = await self._issue(session, issue_id)
        now: datetime = ensure_utc(self.clock.now())
        issue.assignee_member_id = None
        issue.assignee_team_id = None
        issue.updated_at = now
        issue.needs_review = False
        await cascade_to_children(
            session,
            issue,
            GroupChange(GroupAction.ASSIGN, actor, now),
        )
        await self.issues._history(session, issue.id, IssueHistoryKind.ASSIGNED, actor, now)
        await self.issues._audit(
            session,
            "issue.assigned",
            "issue",
            issue.id,
            "Issue assigned",
            0,
            0,
            actor,
            self.issues._actor_kind(actor),
        )
