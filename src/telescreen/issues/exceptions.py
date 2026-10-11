"""Issue exception requests, approval, revocation, and expiry."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import JsonValue, json_int, json_object, parse_json
from telescreen.core.vocabulary import (
    OPEN_STATUSES,
    ActorKind,
    ErrorCode,
    ExceptionDuration,
    ExceptionStatus,
    IssueHistoryKind,
    IssueResolution,
    IssueStatus,
    NotificationChannel,
    NotificationEvent,
    RuleClass,
    Visibility,
)
from telescreen.db.models import IssueExceptionRow, IssueHistoryRow, IssueRow
from telescreen.issues.recipient_channel import issue_context, preferred_channel
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.directory import (
    OwnerDirectory,
    RecipientResolver,
)
from telescreen.ports.notifications import (
    NotificationContext,
    NotificationRequest,
    NotificationSender,
)
from telescreen.ports.policy import PolicyDecider, PolicyDecision, PolicySubject


@dataclass
class ExceptionService:
    """Manage issue exception approval and expiry in caller transactions."""

    audit: AuditLog
    clock: Clock
    redactor: Redactor
    owners: OwnerDirectory
    resolver: RecipientResolver
    decider: PolicyDecider
    default_team: str | None = None
    notifications: NotificationSender | None = None

    async def request(
        self,
        session: AsyncSession,
        issue_id: str,
        reason: str,
        duration: ExceptionDuration,
        actor_ref: str,
        is_admin: bool,
    ) -> str:
        """Request an exception and notify its configured approver safely."""
        issue: IssueRow = await self._required(session, issue_id)
        self._require_open(issue)
        if duration is ExceptionDuration.FOREVER and not is_admin:
            raise TelescreenError(ErrorCode.FORBIDDEN, "administrator permission required")
        safe_reason: str = self.redactor.redact(reason).text[:2000]
        if not safe_reason.strip():
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "exception reason required")
        existing: IssueExceptionRow | None = await session.scalar(
            select(IssueExceptionRow).where(
                IssueExceptionRow.issue_id == issue_id,
                IssueExceptionRow.status.in_(
                    (ExceptionStatus.REQUESTED.value, ExceptionStatus.ACTIVE.value)
                ),
            )
        )
        if existing is not None:
            raise TelescreenError(ErrorCode.CONFLICT, "issue already has an active exception")
        now: datetime = self.clock.now()
        approver: str | None = await self._approver_ref(session, issue)
        row: IssueExceptionRow = IssueExceptionRow(
            id=new_id(),
            issue_id=issue.id,
            reason=safe_reason,
            duration=duration.value,
            status=(ExceptionStatus.ACTIVE if is_admin else ExceptionStatus.REQUESTED).value,
            requested_by=actor_ref[:256],
            approver_ref=actor_ref[:256] if is_admin else approver,
            decided_by=actor_ref[:256] if is_admin else None,
            decided_at=now if is_admin else None,
            expires_at=_expires_at(duration, now),
            created_at=now,
        )
        session.add(row)
        kind: IssueHistoryKind = (
            IssueHistoryKind.EXCEPTION_APPROVED
            if is_admin
            else IssueHistoryKind.EXCEPTION_REQUESTED
        )
        if is_admin:
            issue.status = IssueStatus.IGNORED.value
            issue.resolution = IssueResolution.ACCEPTED_RISK.value
            issue.resolved_at = None
            issue.updated_at = now
        await self._history(session, issue.id, kind, actor_ref, now)
        await self._audit(session, "issue.exception_requested", issue.id, actor_ref)
        if not is_admin and approver is not None:
            await self._notify(
                session,
                approver,
                NotificationEvent.EXCEPTION_REQUESTED,
                issue_context(issue),
                actor_ref,
                issue.id,
            )
        return row.id

    async def approve(
        self,
        session: AsyncSession,
        exception_id: str,
        actor_ref: str,
        member_id: str | None,
        is_admin: bool,
        note: str | None = None,
    ) -> None:
        """Approve a pending request and put its issue in ignored status."""
        row: IssueExceptionRow = await self._exception(session, exception_id)
        if row.status != ExceptionStatus.REQUESTED.value:
            raise TelescreenError(ErrorCode.CONFLICT, "exception is not pending")
        issue: IssueRow = await self._required(session, row.issue_id)
        self._require_open(issue)
        await self._authorize_approver(session, row, actor_ref, member_id, is_admin)
        if not is_admin and row.requested_by == actor_ref:
            raise TelescreenError(ErrorCode.FORBIDDEN, "requester cannot approve own exception")
        now: datetime = self.clock.now()
        row.status = ExceptionStatus.ACTIVE.value
        row.decided_by = actor_ref[:256]
        row.decided_at = now
        row.expires_at = _expires_at(ExceptionDuration(row.duration), now)
        issue.status = IssueStatus.IGNORED.value
        issue.resolution = IssueResolution.ACCEPTED_RISK.value
        issue.resolved_at = None
        issue.updated_at = now
        await self._history(
            session, issue.id, IssueHistoryKind.EXCEPTION_APPROVED, actor_ref, now, note
        )
        await self._audit(session, "issue.exception_approved", issue.id, actor_ref)

    async def reject(
        self,
        session: AsyncSession,
        exception_id: str,
        actor_ref: str,
        member_id: str | None,
        is_admin: bool,
        note: str | None = None,
    ) -> None:
        """Reject a pending exception request without changing issue status."""
        row: IssueExceptionRow = await self._exception(session, exception_id)
        if row.status != ExceptionStatus.REQUESTED.value:
            raise TelescreenError(ErrorCode.CONFLICT, "exception is not pending")
        issue: IssueRow = await self._required(session, row.issue_id)
        await self._authorize_approver(session, row, actor_ref, member_id, is_admin)
        now: datetime = self.clock.now()
        row.status = ExceptionStatus.REJECTED.value
        row.decided_by = actor_ref[:256]
        row.decided_at = now
        await self._history(
            session, issue.id, IssueHistoryKind.EXCEPTION_REJECTED, actor_ref, now, note
        )
        await self._audit(session, "issue.exception_rejected", issue.id, actor_ref)

    async def revoke(
        self,
        session: AsyncSession,
        exception_id: str,
        actor_ref: str,
        member_id: str | None,
        is_admin: bool,
    ) -> None:
        """Revoke a requested or active exception and reopen an ignored issue."""
        row: IssueExceptionRow = await self._exception(session, exception_id)
        if row.status not in (ExceptionStatus.REQUESTED.value, ExceptionStatus.ACTIVE.value):
            raise TelescreenError(ErrorCode.CONFLICT, "exception is not active")
        permitted: bool = (
            is_admin
            or row.requested_by == actor_ref
            or (row.approver_ref is not None and row.approver_ref == actor_ref)
        )
        if not permitted and row.approver_ref is not None and member_id is not None:
            permitted = await self.resolver.is_member_of(session, member_id, row.approver_ref)
        if not permitted:
            raise TelescreenError(ErrorCode.FORBIDDEN, "exception permission required")
        issue: IssueRow = await self._required(session, row.issue_id)
        now: datetime = self.clock.now()
        row.status = ExceptionStatus.REVOKED.value
        row.decided_by = actor_ref[:256]
        row.decided_at = now
        if issue.status == IssueStatus.IGNORED.value:
            issue.status = IssueStatus.UNRESOLVED.value
            issue.resolution = None
            issue.resolved_at = None
            issue.updated_at = now
        await self._history(session, issue.id, IssueHistoryKind.EXCEPTION_REVOKED, actor_ref, now)
        await self._audit(session, "issue.exception_revoked", issue.id, actor_ref)

    async def list(
        self,
        session: AsyncSession,
        status: ExceptionStatus | None,
        issue_id: str | None,
        approver_ref: str | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[tuple[IssueExceptionRow, ...], str | None]:
        """Return a stable page of exception rows and an opaque id cursor."""
        statement: Select[tuple[IssueExceptionRow]] = select(IssueExceptionRow)
        if status is not None:
            statement = statement.where(IssueExceptionRow.status == status.value)
        if issue_id is not None:
            statement = statement.where(IssueExceptionRow.issue_id == issue_id)
        if approver_ref is not None:
            statement = statement.where(IssueExceptionRow.approver_ref == approver_ref)
        if cursor is not None:
            statement = statement.where(IssueExceptionRow.id > cursor)
        rows: tuple[IssueExceptionRow, ...] = tuple(
            (
                await session.scalars(
                    statement.order_by(IssueExceptionRow.id).limit(max(1, min(limit, 100)) + 1)
                )
            ).all()
        )
        page: tuple[IssueExceptionRow, ...] = rows[: max(1, min(limit, 100))]
        next_cursor: str | None = page[-1].id if len(rows) > len(page) and page else None
        return page, next_cursor

    async def expire_due(self, session: AsyncSession, now: datetime) -> int:
        """Expire due date-based exceptions, reopening only still-ignored issues."""
        rows: tuple[IssueExceptionRow, ...] = tuple(
            (
                await session.scalars(
                    select(IssueExceptionRow)
                    .where(
                        IssueExceptionRow.status == ExceptionStatus.ACTIVE.value,
                        IssueExceptionRow.expires_at.is_not(None),
                        IssueExceptionRow.expires_at <= now,
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
        )
        changed: int = 0
        for row in rows:
            issue: IssueRow | None = await session.get(IssueRow, row.issue_id)
            if issue is None:
                continue
            row.status = ExceptionStatus.EXPIRED.value
            row.decided_by = "system"
            row.decided_at = now
            await self._history(
                session, issue.id, IssueHistoryKind.EXCEPTION_EXPIRED, "system:exceptions", now
            )
            await self._audit(session, "issue.exception_expired", issue.id, "system:exceptions")
            if issue.status == IssueStatus.IGNORED.value:
                issue.status = IssueStatus.UNRESOLVED.value
                issue.resolution = None
                issue.resolved_at = None
                issue.updated_at = now
                await self._notify_assignee(session, issue)
            changed += 1
        return changed

    async def _approver_ref(self, session: AsyncSession, issue: IssueRow) -> str | None:
        """Find the configured team lead reference for this issue."""
        decision: PolicyDecision = await self.decider.decide(
            session, _subject(issue), self.clock.now()
        )
        team_slug: str | None = decision.owner_team
        if team_slug is None and issue.assignee_team_id is not None:
            team_slug = await self.owners.team_ref(session, issue.assignee_team_id)
        team_slug = team_slug or self.default_team
        if team_slug is None:
            return None
        member_id: str | None = await self.owners.team_lead(session, team_slug)
        return await self.owners.member_ref(session, member_id) if member_id is not None else None

    async def _authorize_approver(
        self,
        session: AsyncSession,
        row: IssueExceptionRow,
        actor_ref: str,
        member_id: str | None,
        is_admin: bool,
    ) -> None:
        """Require the stored lead or an administrator."""
        if is_admin:
            return
        if row.approver_ref is None or member_id is None:
            raise TelescreenError(ErrorCode.FORBIDDEN, "exception approver required")
        if not await self.resolver.is_member_of(session, member_id, row.approver_ref):
            raise TelescreenError(ErrorCode.FORBIDDEN, "exception approver required")
        if row.requested_by == actor_ref:
            raise TelescreenError(ErrorCode.FORBIDDEN, "requester cannot approve own exception")

    async def _notify_assignee(self, session: AsyncSession, issue: IssueRow) -> None:
        """Notify an assigned member or team using only safe issue metadata."""
        ref: str | None = None
        if issue.assignee_member_id is not None:
            ref = await self.owners.member_ref(session, issue.assignee_member_id)
        elif issue.assignee_team_id is not None:
            ref = await self.owners.team_ref(session, issue.assignee_team_id)
        if ref is not None:
            await self._notify(
                session,
                ref,
                NotificationEvent.EXCEPTION_EXPIRED,
                issue_context(issue),
                "system:exceptions",
                issue.id,
            )

    async def _notify(
        self,
        session: AsyncSession,
        ref: str,
        event: NotificationEvent,
        context: NotificationContext,
        actor_ref: str,
        issue_id: str,
    ) -> None:
        """Resolve a safe recipient ref and queue a DLP-gated notification."""
        if self.notifications is None:
            return
        channel: NotificationChannel | None = await preferred_channel(self.resolver, session, ref)
        if channel is None:
            return
        addresses: tuple[str, ...] = (ref,)
        await self.notifications.send(
            session,
            NotificationRequest(
                channel,
                addresses,
                "",
                "",
                actor_ref,
                issue_id=issue_id,
                event=event,
                context=context,
            ),
        )

    async def _required(self, session: AsyncSession, issue_id: str) -> IssueRow:
        """Return an issue or a safe not-found error."""
        issue: IssueRow | None = await session.get(IssueRow, issue_id)
        if issue is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
        return issue

    async def _exception(self, session: AsyncSession, exception_id: str) -> IssueExceptionRow:
        """Return an exception or a safe not-found error."""
        row: IssueExceptionRow | None = await session.get(IssueExceptionRow, exception_id)
        if row is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "exception not found")
        return row

    def _require_open(self, issue: IssueRow) -> None:
        """Reject exception operations for issues outside the open state set."""
        if issue.status not in tuple(status.value for status in OPEN_STATUSES):
            raise TelescreenError(ErrorCode.CONFLICT, "issue is not open")

    async def _history(
        self,
        session: AsyncSession,
        issue_id: str,
        kind: IssueHistoryKind,
        actor_ref: str,
        now: datetime,
        note: str | None = None,
    ) -> None:
        """Append a history row with redacted and bounded note text."""
        safe_note: str | None = self.redactor.redact(note).text[:4000] if note else None
        session.add(
            IssueHistoryRow(
                id=new_id(),
                issue_id=issue_id,
                kind=kind.value,
                actor_ref=actor_ref[:256],
                note=safe_note,
                at=now,
            )
        )

    async def _audit(
        self, session: AsyncSession, action: str, issue_id: str, actor_ref: str
    ) -> None:
        """Append count-only audit details without exception reason text."""
        actor_kind: ActorKind = (
            ActorKind.MEMBER if actor_ref.startswith("member:") else ActorKind.TOKEN
        )
        details: JsonValue = json_object((("changed", json_int(1)),))
        await self.audit.append(
            session,
            AuditEntry(
                actor_kind,
                actor_ref[:256],
                action,
                "issue",
                issue_id,
                "Issue exception updated",
                details,
            ),
        )


def _expires_at(duration: ExceptionDuration, now: datetime) -> datetime | None:
    """Return date-based expiry for finite exception durations."""
    days: int | None = {
        ExceptionDuration.DAYS_7: 7,
        ExceptionDuration.DAYS_30: 30,
        ExceptionDuration.DAYS_90: 90,
    }.get(duration)
    return now + timedelta(days=days) if days is not None else None


def _subject(issue: IssueRow) -> PolicySubject:
    """Build a policy subject from persisted keyed placement references."""
    placements: tuple[str, ...] = ()
    if issue.placement_keys_json is not None:
        parsed: JsonValue = parse_json(issue.placement_keys_json)
        array_values: tuple[JsonValue, ...] | None = parsed.as_list()
        if array_values is not None:
            placement_values: list[str] = []
            for value in array_values:
                text: str | None = value.as_str()
                if text is not None:
                    placement_values.append(text)
            placements = tuple(placement_values)
    return PolicySubject(
        issue.source,
        RuleClass(issue.rule_class),
        placements,
        Visibility(issue.exposure_visibility),
        issue.resource_modified_at or issue.first_seen,
    )
