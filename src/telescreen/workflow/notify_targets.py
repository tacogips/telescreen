"""Fan out workflow notifications per target and channel (DSL v2 notification sets).

Requests carry only references, template names and an allow-listed masked context; the
sender renders, redacts and applies the DLP gate once per request.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.json import JsonValue, json_array, json_bool, json_int, json_object, json_str
from telescreen.core.vocabulary import DlpVerdict, NotificationChannel, NotificationEvent
from telescreen.ports.issues import IssueSnapshot
from telescreen.ports.notifications import (
    NotificationContext,
    NotificationReceipt,
    NotificationRequest,
    NotificationSender,
)
from telescreen.workflow.dsl.context import IssueContext
from telescreen.workflow.dsl.model import NotificationTargetSpec


@dataclass(frozen=True)
class TargetOutcome:
    """Row counts for one (target, channel) request; never recipient values."""

    index: int
    channel: NotificationChannel
    queued: int
    blocked: int
    skipped: int
    notification_ids: tuple[str, ...]


@dataclass(frozen=True)
class FanOutText:
    """Event and text sources for fan-out requests, in precedence order.

    Per target the template is ``target.template``, then ``step_template``, then the inline
    text (sent with ``inline=True``), then ``default_template``.
    """

    event: NotificationEvent
    default_template: str | None
    step_template: str | None = None
    inline_subject: str | None = None
    inline_body: str | None = None


def build_workflow_context(
    issue: IssueSnapshot | None,
    workflow_name: str,
    step_name: str,
    task_title: str | None,
    task_due_at: datetime | None,
    task_tier: int | None,
    assignee_ref: str | None,
    public_base_url: str | None,
) -> NotificationContext:
    """Build the masked template context for workflow notification sites."""
    base: str | None = public_base_url.rstrip("/") if public_base_url else None
    return NotificationContext(
        issue_short_id=f"TELE-{issue.number}" if issue is not None else None,
        issue_title=issue.display_title if issue is not None else None,
        issue_rule_class=issue.rule_class.value if issue is not None else None,
        issue_severity=issue.severity.value if issue is not None else None,
        issue_status=issue.status.value if issue is not None else None,
        issue_exposure=issue.visibility.value if issue is not None else None,
        issue_risk_score=issue.risk_score if issue is not None else None,
        issue_location=issue.display_locator if issue is not None else None,
        issue_excerpt=issue.masked_excerpt if issue is not None else None,
        issue_link=f"{base}/issues/{issue.id}" if issue is not None and base else None,
        run_workflow=workflow_name,
        step_name=step_name,
        task_title=task_title,
        task_due_at=task_due_at.isoformat() if task_due_at is not None else None,
        task_tier=task_tier,
        task_link=f"{base}/my-tasks" if base else None,
        assignee_ref=assignee_ref,
    )


def with_run_issue(context: NotificationContext, issue: IssueContext | None) -> NotificationContext:
    """Fill issue fields from the run's masked issue context when no snapshot was read."""
    if issue is None or context.issue_short_id is not None:
        return context
    return replace(
        context,
        issue_short_id=f"TELE-{issue.number}",
        issue_title=issue.title,
        issue_rule_class=issue.rule_class,
        issue_severity=issue.severity,
        issue_status=issue.status,
        issue_location=issue.display_locator,
    )


async def fan_out(
    sender: NotificationSender,
    session: AsyncSession,
    targets: tuple[NotificationTargetSpec, ...],
    set_name: str | None,
    text: FanOutText,
    context: NotificationContext,
    actor_ref: str,
    run_id: str | None,
    issue_id: str | None,
) -> tuple[TargetOutcome, ...]:
    """Send one DLP-gated request per (target index, channel), recording unreachable refs."""
    outcomes: list[TargetOutcome] = []
    for index, target in enumerate(targets):
        template: str | None = target.template or text.step_template
        inline: bool = template is None and (
            text.inline_subject is not None or text.inline_body is not None
        )
        for channel in target.channels:
            request: NotificationRequest = NotificationRequest(
                channel=channel,
                recipients=target.recipients,
                subject=(text.inline_subject or "") if inline else "",
                body=(text.inline_body or "") if inline else "",
                actor_ref=actor_ref,
                run_id=run_id,
                issue_id=issue_id,
                event=text.event,
                template_name=None if inline else (template or text.default_template),
                context=context,
                inline=inline,
                set_name=set_name,
                target_index=index,
                record_unreachable=True,
            )
            receipt: NotificationReceipt = await sender.send(session, request)
            outcomes.append(_outcome(index, channel, receipt))
    return tuple(outcomes)


def _outcome(
    index: int, channel: NotificationChannel, receipt: NotificationReceipt
) -> TargetOutcome:
    gated: tuple[str, ...] = tuple(
        item for item in receipt.notification_ids if item not in receipt.skipped
    )
    blocked: bool = receipt.verdict is DlpVerdict.BLOCKED
    return TargetOutcome(
        index,
        channel,
        len(gated) if receipt.queued and not blocked else 0,
        len(gated) if blocked else 0,
        len(receipt.skipped),
        receipt.notification_ids,
    )


def outcomes_json(outcomes: tuple[TargetOutcome, ...]) -> JsonValue:
    """Return the v2 notify output: queued flag, row ids and per-target counts."""
    return json_object(
        (
            ("queued", json_bool(any(item.queued > 0 for item in outcomes))),
            (
                "notification_ids",
                json_array(
                    tuple(json_str(value) for item in outcomes for value in item.notification_ids)
                ),
            ),
            (
                "targets",
                json_array(
                    tuple(
                        json_object(
                            (
                                ("index", json_int(item.index)),
                                ("channel", json_str(item.channel.value)),
                                ("queued", json_int(item.queued)),
                                ("blocked", json_int(item.blocked)),
                                ("skipped", json_int(item.skipped)),
                            )
                        )
                        for item in outcomes
                    )
                ),
            ),
        )
    )
