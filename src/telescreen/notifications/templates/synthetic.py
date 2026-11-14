"""Fixed synthetic preview contexts built from reserved example values only."""

from dataclasses import replace

from telescreen.core.vocabulary import NotificationEvent
from telescreen.ports.notifications import NotificationContext

_BASE_URL: str = "https://telescreen.example.test"

_SYNTHETIC: NotificationContext = NotificationContext(
    issue_short_id="TELE-123",
    issue_title="Credential in config/app.yml",
    issue_rule_class="credential",
    issue_severity="high",
    issue_status="unresolved",
    issue_exposure="public",
    issue_risk_score=72,
    issue_location="github:example-org/example-repo/config/app.yml",
    issue_excerpt='token = "****"',
    issue_link=f"{_BASE_URL}/issues/example",
    run_workflow="example-workflow",
    step_name="notify-owner",
    task_title="Review TELE-123",
    task_due_at="2026-01-01T00:00:00+00:00",
    task_tier=1,
    task_link=f"{_BASE_URL}/my-tasks",
    assignee_ref="team:example-team",
    digest_summary=(
        "Open issues: unresolved 1\nTELE-123 credential high public risk 72\nApprovals: TELE-124"
    ),
    health_target="example-collector",
    health_condition="stale_success",
    health_details=(
        "kind=github; condition=stale_success; last_success_at=none; "
        "last_failure_at=2026-01-01T00:00:00+00:00; consecutive_failures=3; "
        "public_unscannable=0"
    ),
    alert_rule="Example alert",
    alert_trigger="frequency",
    alert_count="10",
    alert_window="1h",
    activity_kind="commented",
    activity_actor="Example Member",
    comment_excerpt="Example comment",
    report_summary="Open issues: 3",
    report_assigned_open="1",
)


def synthetic_context(event: NotificationEvent) -> NotificationContext:
    """Return the synthetic preview context for an event kind.

    Rendering substitutes only the variables allowed for the event, so one shared
    constant serves every event; Police alerts get a worker target instead.
    """
    if event is NotificationEvent.POLICE_HEALTH_ALERT:
        return replace(
            _SYNTHETIC,
            health_target="example-police-worker",
            health_condition="police_silent",
            health_details="worker=example-police-worker; condition=police_silent",
        )
    return _SYNTHETIC
