"""Safe mention parsing and notification helpers."""

import re

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import (
    ErrorCode,
    NotificationCategory,
    NotificationEvent,
    SubscriptionReason,
)
from telescreen.db.models import IssueMentionRow
from telescreen.issues.activity.subscriptions import auto_subscribe
from telescreen.ports.directory import AssigneeIds, RecipientResolver
from telescreen.ports.notifications import MemberNotification, MemberNotifier, NotificationContext

_MENTION: re.Pattern[str] = re.compile(r"@([a-z0-9_.-]{1,128})")


def parse_mentions(text: str) -> tuple[str, ...]:
    """Return unique mention handles, capped at ten."""
    handles: list[str] = []
    for match in _MENTION.finditer(text):
        handle: str = match.group(1)
        if handle not in handles:
            handles.append(handle)
        if len(handles) == 10:
            break
    return tuple(handles)


async def add_mentions(
    session: AsyncSession,
    issue_id: str,
    comment_id: str,
    text: str,
    actor_member_id: str | None,
    actor_display: str,
    short_id: str,
    issue_title: str,
    resolver: RecipientResolver,
    notifier: MemberNotifier | None,
    clock: Clock,
    excluded_member_ids: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Persist unique mentions and queue safe notifications for new recipients."""
    member_ids: list[str] = []
    for handle in parse_mentions(text):
        try:
            assignee: AssigneeIds = await resolver.assignee_of(session, f"member:{handle}")
        except TelescreenError as error:
            if error.code is ErrorCode.RECIPIENT_UNKNOWN:
                continue
            raise
        member_id: str | None = assignee.member_id
        if member_id is None or member_id in member_ids or member_id in excluded_member_ids:
            continue
        member_ids.append(member_id)
        session.add(
            IssueMentionRow(comment_id=comment_id, member_id=member_id, created_at=clock.now())
        )
        await auto_subscribe(session, issue_id, member_id, SubscriptionReason.MENTIONED, clock)
        if notifier is not None:
            context: NotificationContext = NotificationContext(
                issue_short_id=short_id,
                issue_title=issue_title,
                activity_actor=actor_display,
                comment_excerpt=text[:200],
            )
            await notifier.send_to_member(
                session,
                MemberNotification(
                    member_id,
                    NotificationCategory.MENTIONS,
                    NotificationEvent.ISSUE_MENTIONED,
                    context,
                    issue_id,
                ),
            )
    return tuple(member_ids)
