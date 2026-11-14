"""Built-in templates reproduce the v1.1 notification text byte for byte."""

import pytest

from telescreen.core.vocabulary import (
    HealthCondition,
    NotificationChannel,
    NotificationEvent,
)
from telescreen.db.models import IssueRow
from telescreen.issues.keys import short_id
from telescreen.issues.recipient_channel import issue_context
from telescreen.notifications.templates.builtins import BUILTIN_TEMPLATES, builtin_for
from telescreen.notifications.templates.render import render_for_channel
from telescreen.ports.notifications import NotificationContext
from tests.notifications.v12.fakes import make_issue

CHANNELS: tuple[NotificationChannel, ...] = (NotificationChannel.EMAIL, NotificationChannel.SLACK)


def rendered(
    event: NotificationEvent, context: NotificationContext, channel: NotificationChannel
) -> tuple[str, str]:
    """Render an event's built-in template for one channel."""
    return render_for_channel(builtin_for(event).texts, channel, context, event)


def test_one_builtin_per_event_named_system_event() -> None:
    """Every event kind has exactly one built-in, version 0, named system.<event>."""
    assert sorted(template.event_kind.value for template in BUILTIN_TEMPLATES) == sorted(
        event.value for event in NotificationEvent
    )
    for template in BUILTIN_TEMPLATES:
        assert template.name == f"system.{template.event_kind.value}"
        assert template.version == 0


@pytest.mark.parametrize("channel", CHANNELS)
@pytest.mark.parametrize(
    ("event", "v11_subject"),
    (
        (NotificationEvent.VERIFICATION_INCONCLUSIVE, "Verification inconclusive for {short}"),
        (NotificationEvent.EXCEPTION_REQUESTED, "Exception requested for {short}"),
        (NotificationEvent.EXCEPTION_EXPIRED, "Exception expired for {short}"),
    ),
)
def test_issue_event_parity(
    event: NotificationEvent, v11_subject: str, channel: NotificationChannel
) -> None:
    """Verification and exception notifications match the v1.1 f-strings."""
    issue: IssueRow = make_issue(7)
    short: str = short_id(issue.number)
    v11_body: str = f"{short} {issue.rule_class} {issue.severity} {issue.exposure_visibility}"
    assert rendered(event, issue_context(issue), channel) == (
        v11_subject.format(short=short),
        v11_body,
    )


@pytest.mark.parametrize("channel", CHANNELS)
def test_owner_digest_parity(channel: NotificationChannel) -> None:
    """The digest summary is the v1.1 body under the v1.1 subject."""
    body: str = "Open issues: unresolved 1\nTELE-1 token high public risk 72\nApprovals: TELE-2"
    assert rendered(
        NotificationEvent.OWNER_DIGEST, NotificationContext(digest_summary=body), channel
    ) == ("Telescreen owner digest", body)


@pytest.mark.parametrize("channel", CHANNELS)
def test_collector_health_parity(channel: NotificationChannel) -> None:
    """Collector health alerts match the v1.1 subject and key=value body."""
    name: str = "github-org"
    condition: HealthCondition = HealthCondition.CONSECUTIVE_FAILURES
    body: str = (
        f"kind=github; condition={condition.value}; "
        "last_success_at=none; last_failure_at=2026-10-10T12:00:00+00:00; "
        "consecutive_failures=3; public_unscannable=0"
    )
    context: NotificationContext = NotificationContext(
        health_target=name, health_condition=condition.value, health_details=body
    )
    assert rendered(NotificationEvent.COLLECTOR_HEALTH_ALERT, context, channel) == (
        f"Telescreen health: {name} {condition.value}",
        body,
    )


@pytest.mark.parametrize("channel", CHANNELS)
def test_police_health_parity(channel: NotificationChannel) -> None:
    """Police silence alerts match the v1.1 subject and body."""
    name: str = "police-1"
    context: NotificationContext = NotificationContext(
        health_target=name, health_condition=HealthCondition.POLICE_SILENT.value
    )
    assert rendered(NotificationEvent.POLICE_HEALTH_ALERT, context, channel) == (
        f"Telescreen health: police {name} police_silent",
        f"Police worker {name} is silent.",
    )


@pytest.mark.parametrize("channel", CHANNELS)
@pytest.mark.parametrize(
    ("event", "v11_subject"),
    (
        (NotificationEvent.HUMAN_TASK_ASSIGNED, "Workflow task assigned"),
        (NotificationEvent.SLA_ESCALATED, "Workflow task escalated"),
    ),
)
def test_task_event_parity(
    event: NotificationEvent, v11_subject: str, channel: NotificationChannel
) -> None:
    """Task assignment and escalation keep the v1.1 subject and the task title body."""
    title: str = "Review TELE-3 rotation"
    assert rendered(event, NotificationContext(task_title=title), channel) == (
        v11_subject,
        title,
    )


@pytest.mark.parametrize("channel", CHANNELS)
@pytest.mark.parametrize("base_url", (None, "https://telescreen.example.test/"))
def test_owner_review_parity(channel: NotificationChannel, base_url: str | None) -> None:
    """Owner review text matches v1.1 with and without the optional task link line."""
    issue: IssueRow = make_issue(5)
    snapshot_short_id: str = short_id(issue.number)
    body_lines: list[str] = [
        f"TELE-{issue.number} {snapshot_short_id}",
        f"{issue.rule_class} {issue.severity} {issue.exposure_visibility}",
        f"risk {issue.risk_score}",
    ]
    if base_url is not None:
        body_lines.append(f"{base_url.rstrip('/')}/my-tasks")
    context: NotificationContext = NotificationContext(
        issue_short_id=snapshot_short_id,
        issue_rule_class=issue.rule_class,
        issue_severity=issue.severity,
        issue_exposure=issue.exposure_visibility,
        issue_risk_score=issue.risk_score,
        task_link=f"{base_url.rstrip('/')}/my-tasks" if base_url is not None else None,
    )
    assert rendered(NotificationEvent.OWNER_REVIEW_ASSIGNED, context, channel) == (
        f"Telescreen: review TELE-{issue.number}",
        "\n".join(body_lines),
    )
