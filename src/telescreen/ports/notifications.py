"""Outbound notification port and DTOs."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.vocabulary import (
    DlpVerdict,
    ErrorCode,
    NotificationCategory,
    NotificationChannel,
    NotificationEvent,
)
from telescreen.ports.notification_types import (
    NotificationContext as NotificationContext,
)
from telescreen.ports.notification_types import (
    ProducerRedactionHits,
)


@dataclass(frozen=True)
class NotificationRequest:
    """Masked notification request to pass through the outbound DLP gate.

    With no event, subject and body retain the v1 inline behavior. An event with
    inline false selects a template using template_name and context; an event with
    inline true uses subject and body as supplied while recording the event. The
    set name and target index identify fan-out rows, and record_unreachable asks
    the sender to persist an unreachable recipient outcome.

    Optional id seeds and prior-hit counts default to the existing single-process behavior.
    """

    channel: NotificationChannel
    recipients: tuple[str, ...]
    subject: str
    body: str
    actor_ref: str
    run_id: str | None = None
    issue_id: str | None = None
    event: NotificationEvent | None = None
    template_name: str | None = None
    context: NotificationContext | None = None
    inline: bool = False
    set_name: str | None = None
    target_index: int | None = None
    record_unreachable: bool = False
    category: NotificationCategory | None = None
    id_seed: str | None = None
    prior_hits: ProducerRedactionHits | None = None
    skip_unknown: bool = False


@dataclass(frozen=True)
class NotificationReceipt:
    """Safe notification identifiers and DLP decision; pending defaults to false."""

    notification_ids: tuple[str, ...]
    verdict: DlpVerdict
    queued: bool
    skipped: tuple[str, ...] = ()
    pending: bool = False


class NotificationSender(Protocol):
    """Queue a notification in the caller transaction."""

    async def send(
        self, session: AsyncSession, request: NotificationRequest
    ) -> NotificationReceipt:
        """Apply DLP and queue a notification."""
        ...


@dataclass(frozen=True)
class MemberNotification:
    """One member-directed notification with optional delivery metadata."""

    member_id: str
    category: NotificationCategory
    event: NotificationEvent
    context: NotificationContext
    issue_id: str | None = None
    id_seed: str | None = None
    prior_hits: ProducerRedactionHits | None = None


class MemberNotifier(Protocol):
    """Send a category-tagged notification to one member."""

    async def send_to_member(
        self, session: AsyncSession, notification: MemberNotification
    ) -> NotificationReceipt:
        """Queue the member notification in the caller transaction."""
        ...


@dataclass(frozen=True)
class TestOutcome:
    """Safe outcome of a notification channel test."""

    ok: bool
    error_code: ErrorCode | None
    notification_id: str | None


class NotificationTester(Protocol):
    """Send a test notification using its own transaction."""

    async def send_test(self, channel: NotificationChannel, member_id: str) -> TestOutcome:
        """Send one test to the specified member and channel."""
        ...


@dataclass(frozen=True)
class GuardVerdict:
    """Count-only result of inspecting outbound content."""

    allowed: bool
    hit_count: int


class OutboundContentGuard(Protocol):
    """Inspect content before it leaves the application boundary."""

    async def inspect(self, text: str) -> GuardVerdict:
        """Return whether content passes outbound checks without returning matches."""
        ...
