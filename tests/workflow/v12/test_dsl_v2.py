"""Workflow DSL v2 notification sets: loader, validation, compiled JSON and escalation tiers."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from telescreen.core.json import dump_json
from telescreen.core.vocabulary import NotificationChannel, SourceFormat
from telescreen.workflow.dsl.escalation import (
    EscalationPolicySpec,
    escalation_from_json,
    escalation_to_json,
    load_escalation_policy,
)
from telescreen.workflow.dsl.loader import (
    WorkflowValidationError,
    compiled_from_json,
    compiled_to_json,
    load_workflow,
)
from telescreen.workflow.dsl.model import (
    CompiledWorkflow,
    HumanTaskStep,
    NotificationTargetSpec,
    NotifyStep,
    StepSpec,
)
from telescreen.workflow.dsl.schema import workflow_schema_text
from telescreen.workflow.dsl.validate import ValidationIssue

V2_WORKFLOW: str = """apiVersion: telescreen.workflow/v2
kind: Workflow
name: secops-v2
start: alert
notification_sets:
  - name: secops-alert
    targets:
      - {recipients: [team:secops, slack_channel:C0123ABCDE], channels: [slack]}
      - {recipients: [email:secops-leads@example.invalid], channels: [email], template: leak-mail}
steps:
  - {id: alert, type: notify, notification_set: secops-alert, template: leak-alert, next: triage}
  - id: triage
    type: human_task
    title: Triage
    assignee: team:secops
    notify_set: secops-alert
    outcomes: {done: done}
  - {id: done, type: end, result: succeeded}
"""


def _issues(source: str) -> tuple[ValidationIssue, ...]:
    with pytest.raises(WorkflowValidationError) as caught:
        load_workflow(source, SourceFormat.YAML)
    return caught.value.issues


def _has(issues: tuple[ValidationIssue, ...], code: str, path: str) -> bool:
    return any(item.code == code and item.path == path for item in issues)


def test_v1_document_with_v2_fields_requires_api_version_v2() -> None:
    """Sets and set references are rejected in a v1 document at their own paths."""
    source: str = V2_WORKFLOW.replace("telescreen.workflow/v2", "telescreen.workflow/v1")
    issues: tuple[ValidationIssue, ...] = _issues(source)
    assert _has(issues, "requires_api_version_v2", "/notification_sets")
    assert _has(issues, "requires_api_version_v2", "/steps/0/notification_set")
    assert _has(issues, "requires_api_version_v2", "/steps/0/template")
    assert _has(issues, "requires_api_version_v2", "/steps/1/notify_set")


def test_v2_sets_round_trip_and_decoded_steps_carry_set_targets() -> None:
    """The stored (decoded) form keeps set targets, so the engine path fans out."""
    workflow: CompiledWorkflow = load_workflow(V2_WORKFLOW, SourceFormat.YAML)
    assert workflow.api_version == "telescreen.workflow/v2"
    stored: str = dump_json(compiled_to_json(workflow))
    assert '"apiVersion":"telescreen.workflow/v2"' in stored.replace(" ", "")
    assert "set_targets" not in stored
    decoded: CompiledWorkflow = compiled_from_json(compiled_to_json(workflow))
    assert decoded == workflow
    notify: StepSpec = decoded.step("alert")
    assert isinstance(notify, NotifyStep)
    assert len(notify.set_targets) == 2
    assert notify.set_targets[1].template == "leak-mail"
    assert notify.template == "leak-alert"
    triage: StepSpec = decoded.step("triage")
    assert isinstance(triage, HumanTaskStep)
    assert triage.notify_set == "secops-alert"
    assert triage.set_targets == notify.set_targets


def test_inline_targets_and_human_notify_targets_round_trip() -> None:
    """Inline notify targets and human-task notify_targets survive compiled JSON."""
    source: str = """apiVersion: telescreen.workflow/v2
kind: Workflow
name: inline-v2
start: alert
steps:
  - id: alert
    type: notify
    targets:
      - {recipients: [member:alice], channels: [email, slack]}
    subject: Leak
    body: Found
    next: upload
  - id: upload
    type: evidence_upload
    title: Upload
    assignee: team:secops
    notify_targets:
      - {recipients: [email:owner@example.invalid], channels: [email]}
    next: done
  - {id: done, type: end, result: succeeded}
"""
    workflow: CompiledWorkflow = load_workflow(source, SourceFormat.YAML)
    assert compiled_from_json(compiled_to_json(workflow)) == workflow
    alert: StepSpec = workflow.step("alert")
    assert isinstance(alert, NotifyStep)
    assert alert.targets == (
        NotificationTargetSpec(
            ("member:alice",), (NotificationChannel.EMAIL, NotificationChannel.SLACK)
        ),
    )
    assert alert.set_targets == ()


@pytest.mark.parametrize(
    ("old", "new", "code", "path"),
    [
        (
            "[email:secops-leads@example.invalid], channels: [email]",
            "[email:secops-leads@example.invalid], channels: [slack]",
            "undeliverable_combination",
            "/notification_sets/0/targets/1/recipients/0",
        ),
        (
            "slack_channel:C0123ABCDE], channels: [slack]",
            "slack_channel:C0123ABCDE], channels: [email]",
            "undeliverable_combination",
            "/notification_sets/0/targets/0/recipients/1",
        ),
        (
            "slack_channel:C0123ABCDE",
            "slack_channel:general",
            "invalid_recipient",
            "/notification_sets/0/targets/0/recipients/1",
        ),
        (
            "slack_channel:C0123ABCDE",
            "slack_channel:U0123ABCDE",
            "invalid_recipient",
            "/notification_sets/0/targets/0/recipients/1",
        ),
        (
            "slack_channel:C0123ABCDE",
            "slack_channel:D0123ABCDE",
            "invalid_recipient",
            "/notification_sets/0/targets/0/recipients/1",
        ),
        (
            "email:secops-leads@example.invalid",
            "email:a,b@example.invalid",
            "invalid_recipient",
            "/notification_sets/0/targets/1/recipients/0",
        ),
        (
            "team:secops, slack_channel",
            "pager:secops, slack_channel",
            "invalid_recipient",
            "/notification_sets/0/targets/0/recipients/0",
        ),
        (
            "notification_set: secops-alert, template",
            "notification_set: other-set, template",
            "unknown_notification_set",
            "/steps/0/notification_set",
        ),
        (
            "notify_set: secops-alert",
            "notify_set: missing-set",
            "unknown_notification_set",
            "/steps/1/notify_set",
        ),
        (
            "channels: [email], template",
            "channels: [], template",
            "empty_list",
            "/notification_sets/0/targets/1/channels",
        ),
        (
            "{id: alert, type: notify, notification_set: secops-alert,",
            "{id: alert, type: notify, to: [team:secops], notification_set: secops-alert,",
            "conflicting_fields",
            "/steps/0",
        ),
        (
            "{id: alert, type: notify, notification_set: secops-alert,",
            "{id: alert, type: notify,",
            "missing_field",
            "/steps/0",
        ),
        (
            "template: leak-alert",
            "template: Bad Name",
            "invalid_value",
            "/steps/0/template",
        ),
    ],
)
def test_v2_validation_codes_and_paths(old: str, new: str, code: str, path: str) -> None:
    """Every rejected v2 shape reports a closed code at a JSON path, never values."""
    assert old in V2_WORKFLOW
    issues: tuple[ValidationIssue, ...] = _issues(V2_WORKFLOW.replace(old, new))
    assert _has(issues, code, path), issues
    assert all("example.invalid" not in item.path for item in issues)


def test_duplicate_set_names_are_rejected() -> None:
    """Set names are unique within a workflow."""
    source: str = V2_WORKFLOW.replace(
        "steps:\n",
        "  - {name: secops-alert, targets: [{recipients: [team:x], channels: [email]}]}\nsteps:\n",
        1,
    )
    assert _has(_issues(source), "duplicate_name", "/notification_sets/1/name")


def test_slack_channel_recipient_is_accepted_in_v2_to_form() -> None:
    """A v2 notify step may address a Slack channel id directly with a named template."""
    source: str = V2_WORKFLOW.replace(
        "{id: alert, type: notify, notification_set: secops-alert, template: leak-alert,",
        "{id: alert, type: notify, channel: slack, to: [slack_channel:C0123ABCDE],"
        " template: leak-alert,",
    )
    workflow: CompiledWorkflow = load_workflow(source, SourceFormat.YAML)
    alert: StepSpec = workflow.step("alert")
    assert isinstance(alert, NotifyStep)
    assert alert.to == ("slack_channel:C0123ABCDE",)
    assert alert.subject == ()
    assert compiled_from_json(compiled_to_json(workflow)) == workflow


_V1_EXAMPLES: list[Path] = [
    path
    for path in sorted(Path("examples/workflows").glob("*.yaml"))
    if path.read_text(encoding="utf-8").startswith("apiVersion: telescreen.workflow/v1\n")
]


@pytest.mark.parametrize("workflow_path", _V1_EXAMPLES)
def test_existing_examples_still_load_as_v1(workflow_path: Path) -> None:
    """Existing v1 examples load unchanged and keep the v1 compiled apiVersion."""
    workflow: CompiledWorkflow = load_workflow(
        workflow_path.read_text(encoding="utf-8"), SourceFormat.YAML
    )
    assert workflow.api_version == "telescreen.workflow/v1"
    assert workflow.notification_sets == ()
    stored: str = dump_json(compiled_to_json(workflow))
    assert "telescreen.workflow/v1" in stored
    assert "notification_set" not in stored
    assert "notify_targets" not in stored
    assert compiled_from_json(compiled_to_json(workflow)) == workflow


def test_published_schema_accepts_v1_and_v2_documents() -> None:
    """The served schema is the v2 schema, which also accepts v1 documents."""
    schema: str = workflow_schema_text()
    jsonschema.Draft202012Validator.check_schema(json.loads(schema))
    for version in ("telescreen.workflow/v1", "telescreen.workflow/v2"):
        jsonschema.validate(
            json.loads(
                json.dumps(
                    {
                        "apiVersion": version,
                        "kind": "Workflow",
                        "name": "schema-check",
                        "start": "done",
                        "notification_sets": [
                            {
                                "name": "ops",
                                "targets": [{"recipients": ["team:ops"], "channels": ["email"]}],
                            }
                        ],
                        "steps": [{"id": "done", "type": "end"}],
                    }
                )
            ),
            json.loads(schema),
        )


def test_tier_with_only_notification_set_parses_and_round_trips() -> None:
    """A tier may notify only through a workflow notification set."""
    source: str = """name: security
tiers:
  - after: PT5M
    assign_to: team:on-call
    notification_set: secops-alert
  - after: PT10M
    assign_to: team:leads
    notify: [slack_channel:C0123ABCDE]
    channels: [slack]
    targets:
      - {recipients: [email:leads@example.invalid], channels: [email], template: leak-mail}
"""
    policy: EscalationPolicySpec = load_escalation_policy(source, SourceFormat.YAML)
    assert policy.tiers[0].notification_set == "secops-alert"
    assert policy.tiers[0].notify == ()
    assert policy.tiers[1].notify == ("slack_channel:C0123ABCDE",)
    assert policy.tiers[1].targets[0].template == "leak-mail"
    assert escalation_from_json(escalation_to_json(policy)) == policy


@pytest.mark.parametrize(
    ("tier", "code", "path"),
    [
        ("    channels: [email]\n", "missing_field", "/tiers/0"),
        ("    notify: [team:x]\n", "missing_field", "/tiers/0/channels"),
        (
            "    targets: [{recipients: ['email:a@example.invalid'], channels: [slack]}]\n",
            "undeliverable_combination",
            "/tiers/0/targets/0/recipients/0",
        ),
        ("    notification_set: Bad Set\n", "invalid_value", "/tiers/0/notification_set"),
        (
            "    notify: [slack_channel:U0123ABCDE]\n    channels: [slack]\n",
            "invalid_value",
            "/tiers/0/notify/0",
        ),
    ],
)
def test_invalid_tiers_report_codes(tier: str, code: str, path: str) -> None:
    """A tier needs a notification source, and inline targets are validated."""
    source: str = "name: security\ntiers:\n  - after: PT5M\n    assign_to: team:on-call\n" + tier
    with pytest.raises(WorkflowValidationError) as caught:
        load_escalation_policy(source, SourceFormat.YAML)
    assert _has(caught.value.issues, code, path), caught.value.issues
