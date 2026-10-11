"""Outbound notification port and DTOs."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.vocabulary import DlpVerdict, NotificationChannel, NotificationEvent


@dataclass(frozen=True)
class NotificationContext:
    """Allow-listed, already-masked values available to notification templates."""

    issue_short_id: str | None = None
    issue_title: str | None = None
    issue_rule_class: str | None = None
    issue_severity: str | None = None
    issue_status: str | None = None
    issue_exposure: str | None = None
    issue_risk_score: int | None = None
    issue_location: str | None = None
    issue_excerpt: str | None = None
    issue_link: str | None = None
    run_workflow: str | None = None
    step_name: str | None = None
    task_title: str | None = None
    task_due_at: str | None = None
    task_tier: int | None = None
    task_link: str | None = None
    assignee_ref: str | None = None
    digest_summary: str | None = None
    health_target: str | None = None
    health_condition: str | None = None
    health_details: str | None = None


@dataclass(frozen=True)
class NotificationRequest:
    """Masked notification request to pass through the outbound DLP gate.

    With no event, subject and body retain the v1 inline behavior. An event with
    inline false selects a template using template_name and context; an event with
    inline true uses subject and body as supplied while recording the event. The
    set name and target index identify fan-out rows, and record_unreachable asks
    the sender to persist an unreachable recipient outcome.
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


@dataclass(frozen=True)
class NotificationReceipt:
    """Safe notification identifiers and DLP decision."""

    notification_ids: tuple[str, ...]
    verdict: DlpVerdict
    queued: bool
    skipped: tuple[str, ...] = ()


class NotificationSender(Protocol):
    """Queue a notification in the caller transaction."""

    async def send(
        self, session: AsyncSession, request: NotificationRequest
    ) -> NotificationReceipt:
        """Apply DLP and queue a notification."""
        ...
