"""Safe alert rule validation with field paths and stable codes."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.alerts.model import AlertRule
from telescreen.core.clock import Clock
from telescreen.core.vocabulary import AlertTriggerKind, IssueEventKind, NotificationEvent
from telescreen.masking.redact import Redactor
from telescreen.ports.templates import TemplateCatalog, TemplateInfo
from telescreen.ports.workflow import RunStarter
from telescreen.workflow.dsl.notification_sets import validate_targets
from telescreen.workflow.dsl.validate import ValidationIssue

_RULE_NAME: re.Pattern[str] = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class RuleIssue:
    """A safe validation diagnostic containing no user-provided values."""

    field: str
    code: str


async def validate_rule(
    rule: AlertRule,
    redactor: Redactor,
    clock: Clock,
    catalog: TemplateCatalog | None,
    starter: RunStarter | None,
    session: AsyncSession,
    muted_until: datetime | None = None,
) -> tuple[RuleIssue, ...]:
    """Return safe field-path and code diagnostics for one rule."""
    issues: list[ValidationIssue] = []
    if redactor.has_hits(rule.name):
        issues.append(ValidationIssue("/name", "raw_value_detected"))
        return (RuleIssue("/name", "raw_value_detected"),)
    if not 1 <= len(rule.name) <= 64:
        issues.append(ValidationIssue("/name", "invalid_length"))
    elif _RULE_NAME.fullmatch(rule.name) is None:
        issues.append(ValidationIssue("/name", "invalid_value"))
    trigger: AlertTriggerKind = rule.trigger.kind
    if trigger in (AlertTriggerKind.FREQUENCY, AlertTriggerKind.VALUES):
        if rule.trigger.threshold is None or not 1 <= rule.trigger.threshold <= 100000:
            issues.append(ValidationIssue("/trigger/threshold", "invalid_value"))
        if rule.trigger.window_minutes is None or not 5 <= rule.trigger.window_minutes <= 10080:
            issues.append(ValidationIssue("/trigger/window_minutes", "invalid_value"))
    if not 5 <= rule.action_interval_minutes <= 43200:
        issues.append(ValidationIssue("/action_interval_minutes", "invalid_value"))
    if not rule.actions.targets and rule.actions.start_workflow is None:
        issues.append(ValidationIssue("/actions", "empty_actions"))
    if len(rule.actions.targets) > 10:
        issues.append(ValidationIssue("/actions/targets", "too_many_targets"))
    validate_targets(rule.actions.targets, "/actions/targets", issues)
    for index, target in enumerate(rule.actions.targets):
        if target.template is not None:
            path: str = f"/actions/targets/{index}/template"
            if catalog is None:
                issues.append(ValidationIssue(path, "unknown_template"))
            else:
                template: TemplateInfo | None = await catalog.lookup(session, target.template)
                if template is None or template.active_version is None:
                    issues.append(ValidationIssue(path, "unknown_template"))
                elif template.event_kind is not NotificationEvent.ALERT_FIRED:
                    issues.append(ValidationIssue(path, "template_event_mismatch"))
    if rule.actions.start_workflow is not None:
        events: tuple[IssueEventKind, ...] | None = (
            await starter.trigger_events(session, rule.actions.start_workflow)
            if starter is not None
            else None
        )
        if events is None:
            issues.append(ValidationIssue("/actions/start_workflow", "unknown_workflow"))
        elif trigger in (
            AlertTriggerKind.ISSUE_CREATED,
            AlertTriggerKind.ISSUE_REGRESSED,
            AlertTriggerKind.ISSUE_ESCALATING,
        ) and any(event.value == trigger.value for event in events):
            issues.append(ValidationIssue("/actions/start_workflow", "duplicate_trigger"))
    if muted_until is not None and muted_until > clock.now() + timedelta(days=90):
        issues.append(ValidationIssue("/muted_until", "invalid_value"))
    return tuple(RuleIssue(issue.path, issue.code) for issue in issues)
