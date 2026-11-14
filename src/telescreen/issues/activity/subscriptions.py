"""Persistence helpers for member issue subscriptions."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.vocabulary import SubscriptionReason
from telescreen.db.models import IssueSubscriptionRow


async def set_subscription(
    session: AsyncSession,
    issue_id: str,
    member_id: str,
    subscribed: bool,
    reason: SubscriptionReason,
    clock: Clock,
) -> IssueSubscriptionRow:
    """Set a member's explicit or automatic subscription state."""
    row: IssueSubscriptionRow | None = await session.get(
        IssueSubscriptionRow, (issue_id, member_id)
    )
    if row is None:
        row = IssueSubscriptionRow(
            issue_id=issue_id,
            member_id=member_id,
            subscribed=subscribed,
            reason=reason.value,
            updated_at=clock.now(),
        )
        session.add(row)
    else:
        row.subscribed = subscribed
        row.reason = reason.value
        row.updated_at = clock.now()
    return row


async def auto_subscribe(
    session: AsyncSession,
    issue_id: str,
    member_id: str,
    reason: SubscriptionReason,
    clock: Clock,
) -> IssueSubscriptionRow | None:
    """Subscribe unless the member explicitly unsubscribed (manual reason)."""
    row: IssueSubscriptionRow | None = await session.get(
        IssueSubscriptionRow, (issue_id, member_id)
    )
    if row is not None and not row.subscribed and row.reason == SubscriptionReason.MANUAL.value:
        return row
    return await set_subscription(session, issue_id, member_id, True, reason, clock)


async def subscription_for(
    session: AsyncSession, issue_id: str, member_id: str
) -> IssueSubscriptionRow | None:
    """Read a member's issue subscription."""
    return await session.scalar(
        select(IssueSubscriptionRow).where(
            IssueSubscriptionRow.issue_id == issue_id,
            IssueSubscriptionRow.member_id == member_id,
        )
    )
