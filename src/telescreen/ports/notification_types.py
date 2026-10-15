"""Persistence-independent template context data."""

from dataclasses import dataclass, fields
from typing import get_type_hints

from pydantic import ConfigDict


class _StrictWireModel:
    """Forbid unrecognized fields on notification payload dataclasses."""

    __pydantic_config__ = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class NotificationContext(_StrictWireModel):
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
    alert_rule: str | None = None
    alert_trigger: str | None = None
    alert_count: str | None = None
    alert_window: str | None = None
    activity_kind: str | None = None
    activity_actor: str | None = None
    comment_excerpt: str | None = None
    report_summary: str | None = None
    report_assigned_open: str | None = None
    data_short_id: str | None = None
    data_name: str | None = None
    data_locator: str | None = None
    data_deletion_due: str | None = None
    data_link: str | None = None


@dataclass(frozen=True)
class ContextFieldHits(_StrictWireModel):
    """Count redaction hits associated with one allow-listed context field."""

    field: str
    count: int


@dataclass(frozen=True)
class ProducerRedactionHits(_StrictWireModel):
    """Count-only record of values removed by a notification producer."""

    subject: int
    body: int
    context: tuple[ContextFieldHits, ...] = ()


STRING_CONTEXT_FIELDS: tuple[str, ...] = tuple(
    item.name
    for item in fields(NotificationContext)
    if get_type_hints(NotificationContext)[item.name] == str | None
)
