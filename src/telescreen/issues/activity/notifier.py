"""Member notifications for persisted issue activity."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.vocabulary import (
    NotificationCategory,
    NotificationEvent,
    SubscriptionReason,
)
from telescreen.db.models import IssueRow, IssueSubscriptionRow
from telescreen.issues.activity.subscriptions import auto_subscribe
from telescreen.ports.directory import ActorDirectory
from telescreen.ports.issues import IssueActivity, IssueActivityNotifier
from telescreen.ports.notifications import MemberNotification, MemberNotifier, NotificationContext


@dataclass(frozen=True)
class SubscriptionActivityNotifier(IssueActivityNotifier):
    """Notify the assignee and active subscribers of issue activity."""

    notifier: MemberNotifier
    actors: ActorDirectory
    clock: Clock

    async def on_activity(self, session: AsyncSession, activity: IssueActivity) -> None:
        """Queue activity notifications while excluding the actor and new assignee."""
        issue: IssueRow | None = await session.get(IssueRow, activity.issue_id)
        if issue is None:
            return
        subscribers: tuple[IssueSubscriptionRow, ...] = tuple(
            await session.scalars(
                select(IssueSubscriptionRow).where(
                    IssueSubscriptionRow.issue_id == activity.issue_id,
                    IssueSubscriptionRow.subscribed.is_(True),
                )
            )
        )
        actor_member_id: str | None = (
            activity.actor_ref.removeprefix("member:")
            if activity.actor_ref.startswith("member:")
            else None
        )
        if activity.assignee_member_id is not None:
            await auto_subscribe(
                session,
                activity.issue_id,
                activity.assignee_member_id,
                SubscriptionReason.ASSIGNED,
                self.clock,
            )
            await self.notifier.send_to_member(
                session,
                MemberNotification(
                    activity.assignee_member_id,
                    NotificationCategory.ASSIGNED,
                    NotificationEvent.ISSUE_ASSIGNED,
                    NotificationContext(
                        issue_short_id=f"TELE-{issue.number}", issue_title=issue.display_title
                    ),
                    activity.issue_id,
                ),
            )
        actor_values: tuple[str, ...] = (activity.actor_ref,)
        actor_display: str = (await self.actors.display(session, actor_values))[0].display_name
        for row in subscribers:
            if row.member_id == actor_member_id or row.member_id == activity.assignee_member_id:
                continue
            await self.notifier.send_to_member(
                session,
                MemberNotification(
                    row.member_id,
                    NotificationCategory.SUBSCRIBED,
                    NotificationEvent.ISSUE_ACTIVITY,
                    NotificationContext(
                        issue_short_id=f"TELE-{issue.number}",
                        issue_title=issue.display_title,
                        activity_kind=activity.kind.value,
                        activity_actor=actor_display,
                    ),
                    activity.issue_id,
                ),
            )
