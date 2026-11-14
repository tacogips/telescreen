"""Issue rescan verification task handling."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock
from telescreen.core.ids import new_id
from telescreen.core.json import JsonValue, json_int
from telescreen.core.vocabulary import (
    ActorKind,
    EngineTaskKind,
    ErrorCode,
    IssueHistoryKind,
    IssueStatus,
    NotificationChannel,
    NotificationEvent,
)
from telescreen.db.models import IssueHistoryRow, IssueRow
from telescreen.issues.recipient_channel import issue_context, preferred_channel
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.collectors import RescanKind, RescanResult, ResourceRescanner
from telescreen.ports.directory import OwnerDirectory, RecipientResolver
from telescreen.ports.notifications import NotificationRequest, NotificationSender
from telescreen.ports.workflow import ClaimedTask, TaskHandler, TaskResult


@dataclass(frozen=True)
class VerifyRescanHandler(TaskHandler):
    """Run a read-only issue rescan and record inconclusive outcomes."""

    sessions: async_sessionmaker[AsyncSession]
    rescanner: ResourceRescanner
    audit: AuditLog
    clock: Clock
    owners: OwnerDirectory
    resolver: RecipientResolver
    notifications: NotificationSender
    kind: EngineTaskKind = EngineTaskKind.VERIFY_RESCAN

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """Check eligibility, rescan outside a transaction, and retain uncertain status."""
        issue_id: str = task.ref_id
        async with self.sessions() as session:
            issue: IssueRow | None = await session.get(IssueRow, issue_id)
            if issue is None or issue.status != IssueStatus.PENDING_VERIFICATION.value:
                return TaskResult.done()
        try:
            result: RescanResult = await self.rescanner.rescan(issue_id)
        except Exception:
            result = RescanResult(RescanKind.ERROR, ErrorCode.FETCH_FAILED)
        if result.kind in (RescanKind.FOUND, RescanKind.CLEAN):
            return TaskResult.done()
        async with self.sessions.begin() as session:
            issue = await session.get(IssueRow, issue_id)
            if issue is None or issue.status != IssueStatus.PENDING_VERIFICATION.value:
                return TaskResult.done()
            value: str = (
                result.coverage.value
                if result.coverage is not None
                else result.error_code.value
                if result.error_code is not None
                else ErrorCode.FETCH_FAILED.value
            )
            now: datetime = self.clock.now()
            session.add(
                IssueHistoryRow(
                    id=new_id(),
                    issue_id=issue.id,
                    kind=IssueHistoryKind.VERIFICATION_INCONCLUSIVE.value,
                    actor_ref="system:verify-rescan",
                    to_value=value,
                    at=now,
                )
            )
            details: JsonValue = json_int(1)
            await self.audit.append(
                session,
                AuditEntry(
                    ActorKind.SYSTEM,
                    "system:verify-rescan",
                    "issue.verification_inconclusive",
                    "issue",
                    issue.id,
                    "Issue verification was inconclusive",
                    details,
                ),
            )
            await self._notify(session, issue)
        return TaskResult.done()

    async def _notify(self, session: AsyncSession, issue: IssueRow) -> None:
        """Notify the issue assignee with identifiers and enum metadata only."""
        ref: str | None = None
        if issue.assignee_member_id is not None:
            ref = await self.owners.member_ref(session, issue.assignee_member_id)
        elif issue.assignee_team_id is not None:
            ref = await self.owners.team_ref(session, issue.assignee_team_id)
        if ref is None:
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
                "system:verify-rescan",
                issue_id=issue.id,
                event=NotificationEvent.VERIFICATION_INCONCLUSIVE,
                context=issue_context(issue),
            ),
        )
