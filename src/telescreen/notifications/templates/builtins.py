"""Built-in templates (`system.<event>`, version 0) reproducing the v1.1 notification text."""

from telescreen.core.vocabulary import NotificationEvent
from telescreen.notifications.templates.model import BuiltinTemplate, TemplateTexts

BUILTIN_PREFIX: str = "system."

_ISSUE_LINE: str = (
    "{{ issue.short_id }} {{ issue.rule_class }} {{ issue.severity }} {{ issue.exposure }}"
)


def _builtin(event: NotificationEvent, subject: str, body: str) -> BuiltinTemplate:
    """Create the built-in template of one event kind."""
    return BuiltinTemplate(f"{BUILTIN_PREFIX}{event.value}", event, TemplateTexts(subject, body))


BUILTIN_TEMPLATES: tuple[BuiltinTemplate, ...] = (
    _builtin(
        NotificationEvent.WORKFLOW_NOTIFY,
        "Telescreen: {{ issue.short_id }} {{ issue.rule_class }}",
        "{{ issue.title }}\nseverity {{ issue.severity }}\n{{ issue.location }}\n{{ issue.link }}",
    ),
    _builtin(NotificationEvent.HUMAN_TASK_ASSIGNED, "Workflow task assigned", "{{ task.title }}"),
    _builtin(
        NotificationEvent.OWNER_REVIEW_ASSIGNED,
        "Telescreen: review {{ issue.short_id }}",
        "{{ issue.short_id }} {{ issue.short_id }}\n"
        "{{ issue.rule_class }} {{ issue.severity }} {{ issue.exposure }}\n"
        "risk {{ issue.risk_score }}\n"
        "{{ task.link }}",
    ),
    _builtin(NotificationEvent.SLA_ESCALATED, "Workflow task escalated", "{{ task.title }}"),
    _builtin(NotificationEvent.OWNER_DIGEST, "Telescreen owner digest", "{{ digest.summary }}"),
    _builtin(
        NotificationEvent.EXCEPTION_REQUESTED,
        "Exception requested for {{ issue.short_id }}",
        _ISSUE_LINE,
    ),
    _builtin(
        NotificationEvent.EXCEPTION_EXPIRED,
        "Exception expired for {{ issue.short_id }}",
        _ISSUE_LINE,
    ),
    _builtin(
        NotificationEvent.VERIFICATION_INCONCLUSIVE,
        "Verification inconclusive for {{ issue.short_id }}",
        _ISSUE_LINE,
    ),
    _builtin(
        NotificationEvent.COLLECTOR_HEALTH_ALERT,
        "Telescreen health: {{ health.target }} {{ health.condition }}",
        "{{ health.details }}",
    ),
    _builtin(
        NotificationEvent.POLICE_HEALTH_ALERT,
        "Telescreen health: police {{ health.target }} {{ health.condition }}",
        "Police worker {{ health.target }} is silent.",
    ),
    _builtin(
        NotificationEvent.ALERT_FIRED,
        "Telescreen alert: {{ alert.rule }} {{ issue.short_id }}",
        (
            "{{ issue.title }}\n{{ alert.trigger }} {{ alert.count }} "
            "{{ alert.window }}\n{{ issue.link }}"
        ),
    ),
    _builtin(
        NotificationEvent.ISSUE_ASSIGNED,
        "Assigned to you: {{ issue.short_id }}",
        "{{ issue.title }}\nby {{ activity.actor }}\n{{ issue.link }}",
    ),
    _builtin(
        NotificationEvent.ISSUE_MENTIONED,
        "{{ activity.actor }} mentioned you on {{ issue.short_id }}",
        "{{ comment.excerpt }}\n{{ issue.link }}",
    ),
    _builtin(
        NotificationEvent.ISSUE_ACTIVITY,
        "{{ issue.short_id }}: {{ activity.kind }}",
        "{{ activity.actor }}\n{{ comment.excerpt }}\n{{ issue.link }}",
    ),
    _builtin(
        NotificationEvent.WEEKLY_REPORT,
        "Telescreen weekly report",
        "{{ report.summary }}\nAssigned to you and open: {{ report.assigned_open }}",
    ),
    _builtin(
        NotificationEvent.INTEGRATION_TEST,
        "Telescreen test notification",
        "Test from {{ health.target }}",
    ),
)


def builtin_named(name: str) -> BuiltinTemplate | None:
    """Return the built-in template with this name, if any."""
    return next((template for template in BUILTIN_TEMPLATES if template.name == name), None)


def builtin_for(event: NotificationEvent) -> BuiltinTemplate:
    """Return the built-in template of an event kind."""
    return next(template for template in BUILTIN_TEMPLATES if template.event_kind is event)


def builtin_name(event: NotificationEvent) -> str:
    """Return the reserved built-in name of an event kind."""
    return f"{BUILTIN_PREFIX}{event.value}"
