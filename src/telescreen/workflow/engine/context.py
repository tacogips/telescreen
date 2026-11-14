"""Safe workflow context construction and recursive output redaction."""

from __future__ import annotations

from telescreen.core.json import (
    JsonKind,
    JsonMember,
    JsonValue,
    json_array,
    json_object,
    json_str,
)
from telescreen.masking.redact import Redactor
from telescreen.ports.issues import IssueSnapshot
from telescreen.workflow.dsl.context import IssueContext, RunContext, RunInfo, StepResultContext


def initial_context(
    run_id: str,
    workflow_name: str,
    started_at: str,
    issue: IssueSnapshot | None,
) -> RunContext:
    """Build the initial context from fields already masked by the issue port."""
    issue_context: IssueContext | None = None
    if issue is not None:
        issue_context = IssueContext(
            number=issue.number,
            short_id=issue.short_id,
            title=issue.display_title,
            status=issue.status.value,
            source=issue.source,
            rule_class=issue.rule_class.value,
            severity=issue.severity.value,
            display_locator=issue.display_locator,
            times_seen=issue.times_seen,
            first_seen=issue.first_seen.isoformat(),
            last_seen=issue.last_seen.isoformat(),
        )
    return RunContext(issue_context, RunInfo(run_id, workflow_name, started_at))


def redact_json(value: JsonValue, redactor: Redactor) -> JsonValue:
    """Redact every string leaf before JSON values are stored or returned."""
    if value.kind is JsonKind.STRING:
        return json_str(redactor.redact(value.str_value).text)
    if value.kind is JsonKind.ARRAY:
        return json_array(tuple(redact_json(item, redactor) for item in value.items))
    if value.kind is JsonKind.OBJECT:
        members: tuple[JsonMember, ...] = tuple(
            JsonMember(member.name, redact_json(member.value, redactor)) for member in value.members
        )
        return json_object(tuple((member.name, member.value) for member in members))
    return value


def latest_context(
    context: RunContext,
    step_id: str,
    outcome: str,
    output: JsonValue,
) -> RunContext:
    """Return context with a safe latest result for one workflow step."""
    return context.with_step(StepResultContext(step_id, outcome, output))
