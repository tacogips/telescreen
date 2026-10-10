"""Safe owner and approver digest task handling."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock
from telescreen.core.vocabulary import (
    OPEN_STATUSES,
    EngineTaskKind,
    ExceptionStatus,
    NotificationChannel,
)
from telescreen.db.models import IssueExceptionRow, IssueRow, MemberRow
from telescreen.issues.keys import short_id
from telescreen.issues.recipient_channel import preferred_channel
from telescreen.ports.directory import OwnerDirectory, RecipientResolver
from telescreen.ports.notifications import NotificationRequest, NotificationSender
from telescreen.ports.workflow import ClaimedTask, TaskHandler, TaskResult


@dataclass(frozen=True)
class OwnerDigestHandler(TaskHandler):
    """Deliver per-member open-issue and exception-approval digests."""

    sessions: async_sessionmaker[AsyncSession]
    owners: OwnerDirectory
    resolver: RecipientResolver
    notifications: NotificationSender
    clock: Clock
    kind: EngineTaskKind = EngineTaskKind.OWNER_DIGEST

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """Build safe digests for active members with actionable items."""
        del task
        async with self.sessions.begin() as session:
            members: tuple[MemberRow, ...] = tuple(
                (
                    await session.scalars(
                        select(MemberRow).where(MemberRow.active.is_(True)).order_by(MemberRow.id)
                    )
                ).all()
            )
            for member in members:
                ref: str | None = await self.owners.member_ref(session, member.id)
                if ref is None:
                    continue
                rows: tuple[IssueRow, ...] = tuple(
                    (
                        await session.scalars(
                            select(IssueRow)
                            .where(
                                IssueRow.assignee_member_id == member.id,
                                IssueRow.status.in_(
                                    tuple(status.value for status in OPEN_STATUSES)
                                ),
                            )
                            .order_by(IssueRow.risk_score.desc(), IssueRow.id)
                        )
                    ).all()
                )
                requested: tuple[IssueExceptionRow, ...] = tuple(
                    (
                        await session.scalars(
                            select(IssueExceptionRow).where(
                                IssueExceptionRow.status == ExceptionStatus.REQUESTED.value,
                                IssueExceptionRow.approver_ref == ref,
                            )
                        )
                    ).all()
                )
                requested_ids: list[str] = []
                for row in requested:
                    exception_issue: IssueRow | None = await session.get(IssueRow, row.issue_id)
                    if exception_issue is not None:
                        requested_ids.append(short_id(exception_issue.number))
                expiring: tuple[IssueExceptionRow, ...] = await self._expiring_for_member(
                    session, member.id
                )
                expiring_ids: list[str] = []
                for row in expiring:
                    exception_issue = await session.get(IssueRow, row.issue_id)
                    if exception_issue is not None:
                        expiring_ids.append(short_id(exception_issue.number))
                if not rows and not requested_ids and not expiring_ids:
                    continue
                body: str = self._body(rows, tuple(requested_ids), tuple(expiring_ids))
                await self._send(session, ref, body)
        return TaskResult.done()

    async def _expiring_for_member(
        self, session: AsyncSession, member_id: str
    ) -> tuple[IssueExceptionRow, ...]:
        """Return active finite exceptions assigned to a member and expiring soon."""
        now: datetime = self.clock.now()
        return tuple(
            (
                await session.scalars(
                    select(IssueExceptionRow)
                    .join(IssueRow, IssueRow.id == IssueExceptionRow.issue_id)
                    .where(
                        IssueRow.assignee_member_id == member_id,
                        IssueExceptionRow.status == ExceptionStatus.ACTIVE.value,
                        IssueExceptionRow.expires_at.is_not(None),
                        IssueExceptionRow.expires_at <= now + timedelta(days=7),
                        IssueExceptionRow.expires_at > now,
                    )
                    .order_by(IssueExceptionRow.expires_at)
                )
            ).all()
        )

    async def _send(self, session: AsyncSession, ref: str, body: str) -> None:
        """Send one DLP-gated digest using the member's available channel."""
        channel: NotificationChannel | None = await preferred_channel(self.resolver, session, ref)
        if channel is None:
            return
        addresses: tuple[str, ...] = (ref,)
        await self.notifications.send(
            session,
            NotificationRequest(
                channel,
                addresses,
                "Telescreen owner digest",
                body,
                "system:digest",
            ),
        )

    def _body(
        self,
        issues: tuple[IssueRow, ...],
        requested_ids: tuple[str, ...],
        expiring_ids: tuple[str, ...],
    ) -> str:
        """Render status counts and safe short-id lines without source metadata."""
        counts: list[str] = []
        for status in OPEN_STATUSES:
            count: int = sum(issue.status == status.value for issue in issues)
            if count:
                counts.append(f"{status.value} {count}")
        lines: list[str] = ["Open issues: " + (", ".join(counts) if counts else "0")]
        for issue in issues[:20]:
            lines.append(
                f"{short_id(issue.number)} {issue.rule_class} {issue.severity} "
                f"{issue.exposure_visibility} risk {issue.risk_score}"
            )
        if requested_ids:
            lines.append("Approvals: " + ", ".join(requested_ids[:20]))
        if expiring_ids:
            lines.append("Exceptions expiring: " + ", ".join(expiring_ids[:20]))
        return "\n".join(lines)
