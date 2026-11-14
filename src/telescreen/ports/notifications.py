"""Outbound notification port and DTOs."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.vocabulary import DlpVerdict, NotificationChannel


@dataclass(frozen=True)
class NotificationRequest:
    """Masked notification request to pass through the outbound DLP gate."""

    channel: NotificationChannel
    recipients: tuple[str, ...]
    subject: str
    body: str
    actor_ref: str
    run_id: str | None = None
    issue_id: str | None = None


@dataclass(frozen=True)
class NotificationReceipt:
    """Safe notification identifiers and DLP decision."""

    notification_ids: tuple[str, ...]
    verdict: DlpVerdict
    queued: bool


class NotificationSender(Protocol):
    """Queue a notification in the caller transaction."""

    async def send(
        self, session: AsyncSession, request: NotificationRequest
    ) -> NotificationReceipt:
        """Apply DLP and queue a notification."""
        ...
