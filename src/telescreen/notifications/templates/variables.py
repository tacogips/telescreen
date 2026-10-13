"""Allow-listed template variables and the variable groups allowed per event."""

from telescreen.core.vocabulary import NotificationEvent
from telescreen.ports.notifications import NotificationContext

VARIABLE_FIELDS: tuple[tuple[str, str], ...] = (
    ("issue.short_id", "issue_short_id"),
    ("issue.title", "issue_title"),
    ("issue.rule_class", "issue_rule_class"),
    ("issue.severity", "issue_severity"),
    ("issue.status", "issue_status"),
    ("issue.exposure", "issue_exposure"),
    ("issue.risk_score", "issue_risk_score"),
    ("issue.location", "issue_location"),
    ("issue.excerpt", "issue_excerpt"),
    ("issue.link", "issue_link"),
    ("run.workflow", "run_workflow"),
    ("step.name", "step_name"),
    ("task.title", "task_title"),
    ("task.due_at", "task_due_at"),
    ("task.tier", "task_tier"),
    ("task.link", "task_link"),
    ("assignee.ref", "assignee_ref"),
    ("digest.summary", "digest_summary"),
    ("health.target", "health_target"),
    ("health.condition", "health_condition"),
    ("health.details", "health_details"),
    ("alert.rule", "alert_rule"),
    ("alert.trigger", "alert_trigger"),
    ("alert.count", "alert_count"),
    ("alert.window", "alert_window"),
    ("activity.kind", "activity_kind"),
    ("activity.actor", "activity_actor"),
    ("comment.excerpt", "comment_excerpt"),
    ("report.summary", "report_summary"),
    ("report.assigned_open", "report_assigned_open"),
    ("data.short_id", "data_short_id"),
    ("data.name", "data_name"),
    ("data.locator", "data_locator"),
    ("data.deletion_due", "data_deletion_due"),
    ("data.link", "data_link"),
)

_ISSUE: str = "issue"
_RUN: str = "run"
_STEP: str = "step"
_TASK: str = "task"
_ASSIGNEE: str = "assignee"
_DIGEST: str = "digest"
_HEALTH: str = "health"
_ALERT: str = "alert"
_ACTIVITY: str = "activity"
_COMMENT: str = "comment"
_REPORT: str = "report"
_DATA: str = "data"

EVENT_GROUPS: tuple[tuple[NotificationEvent, tuple[str, ...]], ...] = (
    (NotificationEvent.WORKFLOW_NOTIFY, (_ISSUE, _RUN, _STEP, _TASK, _ASSIGNEE)),
    (NotificationEvent.HUMAN_TASK_ASSIGNED, (_ISSUE, _RUN, _TASK, _ASSIGNEE)),
    (NotificationEvent.OWNER_REVIEW_ASSIGNED, (_ISSUE, _TASK, _ASSIGNEE)),
    (NotificationEvent.SLA_ESCALATED, (_ISSUE, _RUN, _TASK, _ASSIGNEE)),
    (NotificationEvent.OWNER_DIGEST, (_DIGEST,)),
    (NotificationEvent.EXCEPTION_REQUESTED, (_ISSUE,)),
    (NotificationEvent.EXCEPTION_EXPIRED, (_ISSUE,)),
    (NotificationEvent.VERIFICATION_INCONCLUSIVE, (_ISSUE,)),
    (NotificationEvent.COLLECTOR_HEALTH_ALERT, (_HEALTH,)),
    (NotificationEvent.POLICE_HEALTH_ALERT, (_HEALTH,)),
    (NotificationEvent.ALERT_FIRED, (_ALERT, _ISSUE)),
    (NotificationEvent.ISSUE_ASSIGNED, (_ISSUE, _ACTIVITY)),
    (NotificationEvent.ISSUE_MENTIONED, (_ACTIVITY, _COMMENT, _ISSUE)),
    (NotificationEvent.ISSUE_ACTIVITY, (_ISSUE, _ACTIVITY, _COMMENT)),
    (NotificationEvent.WEEKLY_REPORT, (_REPORT,)),
    (NotificationEvent.INTEGRATION_TEST, (_HEALTH,)),
    (NotificationEvent.DATA_OVERDUE, (_DATA,)),
)


def allowed_paths(event: NotificationEvent) -> tuple[str, ...]:
    """Return the variable paths a template of this event kind may reference."""
    groups: tuple[str, ...] = next((pair[1] for pair in EVENT_GROUPS if pair[0] is event), ())
    return tuple(path for path, _ in VARIABLE_FIELDS if path.split(".", 1)[0] in groups)


def lookup(context: NotificationContext, path: str) -> str:
    """Return the text of one allow-listed variable; missing values render empty."""
    field_name: str | None = next((pair[1] for pair in VARIABLE_FIELDS if pair[0] == path), None)
    if field_name is None:
        return ""
    value: str | int | None = getattr(context, field_name)
    if value is None:
        return ""
    if isinstance(value, int):
        return str(value)
    return value
