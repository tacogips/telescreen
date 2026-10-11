"""Save-time template validation reporting only field names and codes."""

import re

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import NotificationEvent
from telescreen.masking.redact import Redactor
from telescreen.notifications.templates.builtins import BUILTIN_PREFIX, builtin_named
from telescreen.notifications.templates.model import (
    TemplateIssue,
    TemplateIssueCode,
    TemplateTexts,
)
from telescreen.notifications.templates.variables import allowed_paths
from telescreen.workflow.dsl.model import Template
from telescreen.workflow.dsl.templates import parse_template

_NAME_PATTERN: re.Pattern[str] = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
SUBJECT_LIMIT: int = 200
BODY_LIMIT: int = 4000
DESCRIPTION_LIMIT: int = 200


def validate_template(
    name: str | None,
    event_kind: NotificationEvent,
    texts: TemplateTexts,
    existing_event: NotificationEvent | None,
    redactor: Redactor,
) -> tuple[TemplateIssue, ...]:
    """Return every validation issue of a template version; empty means valid."""
    issues: list[TemplateIssue] = []
    if name is not None:
        if _NAME_PATTERN.fullmatch(name) is None:
            issues.append(TemplateIssue("name", TemplateIssueCode.INVALID_NAME))
        elif redactor.has_hits(name):
            issues.append(TemplateIssue("name", TemplateIssueCode.RAW_VALUE_DETECTED))
        elif name.startswith(BUILTIN_PREFIX) and builtin_named(name) is None:
            issues.append(TemplateIssue("name", TemplateIssueCode.RESERVED_NAME))
    if existing_event is not None and existing_event is not event_kind:
        issues.append(TemplateIssue("event_kind", TemplateIssueCode.TEMPLATE_EVENT_MISMATCH))
    if texts.description is not None:
        issues.extend(_plain_issues("description", texts.description, DESCRIPTION_LIMIT, redactor))
    allowed: tuple[str, ...] = allowed_paths(event_kind)
    fields: tuple[tuple[str, str | None, int, bool], ...] = (
        ("subject", texts.subject, SUBJECT_LIMIT, False),
        ("body", texts.body, BODY_LIMIT, False),
        ("email_subject", texts.email_subject, SUBJECT_LIMIT, True),
        ("email_body", texts.email_body, BODY_LIMIT, True),
        ("slack_body", texts.slack_body, BODY_LIMIT, True),
    )
    for field_name, value, limit, variant in fields:
        if value is None:
            continue
        if variant and value == "":
            issues.append(TemplateIssue(field_name, TemplateIssueCode.INCOMPLETE_VARIANT))
            continue
        issues.extend(_plain_issues(field_name, value, limit, redactor))
        issues.extend(_template_issues(field_name, value, allowed))
    return tuple(issues)


def _plain_issues(
    field_name: str, value: str, limit: int, redactor: Redactor
) -> tuple[TemplateIssue, ...]:
    """Check length and detected raw values of one text field."""
    issues: list[TemplateIssue] = []
    if len(value) > limit:
        issues.append(TemplateIssue(field_name, TemplateIssueCode.TOO_LONG))
    if redactor.has_hits(value):
        issues.append(TemplateIssue(field_name, TemplateIssueCode.RAW_VALUE_DETECTED))
    return tuple(issues)


def _template_issues(
    field_name: str, value: str, allowed: tuple[str, ...]
) -> tuple[TemplateIssue, ...]:
    """Check template syntax and that every variable is allowed for the event."""
    try:
        template: Template = parse_template(value)
    except TelescreenError:
        return (TemplateIssue(field_name, TemplateIssueCode.INVALID_TEMPLATE),)
    for part in template:
        if part.literal is None and ".".join(part.path) not in allowed:
            return (TemplateIssue(field_name, TemplateIssueCode.UNKNOWN_VARIABLE),)
    return ()
