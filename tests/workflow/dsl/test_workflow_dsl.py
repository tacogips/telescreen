"""Contract tests for the offline workflow DSL."""

from __future__ import annotations

import json

import pytest

from telescreen.core.errors import TelescreenError
from telescreen.core.json import json_bool, json_float, json_object
from telescreen.core.vocabulary import SourceFormat
from telescreen.workflow.dsl.context import IssueContext, RunContext, RunInfo, StepResultContext
from telescreen.workflow.dsl.escalation import (
    EscalationPolicySpec,
    escalation_from_json,
    escalation_to_json,
    load_escalation_policy,
)
from telescreen.workflow.dsl.expressions import (
    EvalResult,
    evaluate,
    parse_expression,
)
from telescreen.workflow.dsl.loader import (
    CompiledWorkflow,
    WorkflowValidationError,
    compiled_from_json,
    compiled_to_json,
    load_workflow,
)
from telescreen.workflow.dsl.model import ConditionStep, Expression, StepSpec
from telescreen.workflow.dsl.schema import workflow_schema_text
from telescreen.workflow.dsl.templates import parse_template, render

VALID_YAML: str = """
apiVersion: telescreen.workflow/v1
kind: Workflow
name: triage
start: branch
steps:
  - id: branch
    type: condition
    cases:
      - when: 'issue.severity == "high"'
        next: high
    default: clean
  - id: high
    type: end
    result: succeeded
  - id: clean
    type: end
    result: failed
"""

ALL_STEPS_YAML: str = """
apiVersion: telescreen.workflow/v1
kind: Workflow
name: all-steps
defaults:
  max_step_visits: 30
  retry: {max_attempts: 3, backoff: PT10S}
start: detect
steps:
  - id: detect
    type: detect
    detectors: [rule]
    outcomes: {found: agent, clean: clean, error: failed}
  - id: agent
    type: ai_agent
    provider: managed
    prompt: 'Verify {{ issue.short_id }}'
    output: [{name: verified, type: boolean}]
    next: condition
  - id: condition
    type: condition
    cases: [{when: 'steps.agent.output.verified == true', next: human}]
    default: notify
  - id: human
    type: human_task
    title: Triage
    assignee: team:security
    outcomes: {handled: evidence}
    sla: PT4H
    escalation_policy: security
  - id: notify
    type: notify
    channel: email
    to: [email:security@example.invalid]
    subject: 'Issue {{ issue.short_id }}'
    body: 'Review {{ issue.display_locator }}'
    next: clean
  - id: evidence
    type: evidence_upload
    title: Upload proof
    assignee: member:owner
    min_files: 1
    next: police
  - id: police
    type: police_verify
    task_type: verify_remediation
    labels: [github]
    input: {locator: '{{ issue.display_locator }}'}
    timeout: PT1H
    outcomes: {passed: map, failed: send-back, error: failed}
  - id: send-back
    type: send_back
    to: human
    reason: 'Verification failed for {{ issue.short_id }}'
    max_send_backs: 2
    on_exhausted: failed
  - id: map
    type: map
    source:
      partition: {via: collector, collector: github}
    map: {via: collector}
    max_concurrency: 8
    shard_timeout: PT5M
    shard_max_attempts: 3
    failure: {max_failed_ratio: 0.1, max_failed_items: 2}
    timeout: PT1H
    outcomes: {succeeded: parallel, partial: clean, failed: failed}
  - id: parallel
    type: parallel
    branches: [timer, child]
    join: joined
  - id: timer
    type: wait
    duration: PT1M
    next: joined
  - id: child
    type: sub_workflow
    workflow: verify
    outcomes: {succeeded: joined, failed: joined}
  - id: joined
    type: join
    mode: all
    next: success
  - id: success
    type: end
    result: succeeded
  - id: clean
    type: end
    result: succeeded
  - id: failed
    type: end
    result: failed
"""


def test_yaml_loads_and_round_trips_through_closed_json() -> None:
    workflow: CompiledWorkflow = load_workflow(VALID_YAML, SourceFormat.YAML)
    restored: CompiledWorkflow = compiled_from_json(compiled_to_json(workflow))
    assert restored == workflow
    with pytest.raises(KeyError, match="missing"):
        workflow.step("missing")


def test_every_step_type_loads_and_round_trips() -> None:
    workflow: CompiledWorkflow = load_workflow(ALL_STEPS_YAML, SourceFormat.YAML)
    assert len(workflow.steps) == 16
    assert compiled_from_json(compiled_to_json(workflow)) == workflow


def test_json_document_loads_identically_to_yaml() -> None:
    workflow: CompiledWorkflow = load_workflow(VALID_YAML, SourceFormat.YAML)
    encoded: str = json.dumps(
        {
            "apiVersion": "telescreen.workflow/v1",
            "kind": "Workflow",
            "name": "triage",
            "start": "branch",
            "steps": [
                {
                    "id": "branch",
                    "type": "condition",
                    "cases": [{"when": 'issue.severity == "high"', "next": "high"}],
                    "default": "clean",
                },
                {"id": "high", "type": "end", "result": "succeeded"},
                {"id": "clean", "type": "end", "result": "failed"},
            ],
        }
    )
    assert load_workflow(encoded, SourceFormat.JSON) == workflow


def test_expression_and_template_use_masked_context() -> None:
    ctx: RunContext = RunContext(
        IssueContext(
            7,
            "TELE-7",
            "Masked title",
            "unresolved",
            "github",
            "credential",
            "high",
            "repo/path",
            2,
            "t1",
            "t2",
        ),
        RunInfo("run-1", "triage", "t0"),
        (StepResultContext("verify", "found", json_object((("is_credential", json_bool(True)),))),),
    )
    expression: Expression = parse_expression(
        'issue.severity == "high" and steps.verify.outcome in ["found", "clean"]'
    )
    assert evaluate(expression, ctx).value.as_bool() is True
    assert (
        evaluate(parse_expression('"missing" not in ["found", "clean"]'), ctx).value.as_bool()
        is True
    )
    mismatch: EvalResult = evaluate(parse_expression('issue.times_seen > "x"'), ctx)
    assert mismatch.value.as_bool() is False
    assert mismatch.note == "type_mismatch"
    equality_mismatch: EvalResult = evaluate(parse_expression('1 == "1"'), ctx)
    assert equality_mismatch.value.as_bool() is False
    assert equality_mismatch.note == "type_mismatch"
    inequality_mismatch: EvalResult = evaluate(parse_expression('1 != "1"'), ctx)
    assert inequality_mismatch.value.as_bool() is False
    assert inequality_mismatch.note == "type_mismatch"
    assert (
        render(parse_template("Issue {{ issue.short_id }} / {{ issue.missing }}"), ctx)
        == "Issue TELE-7 / "
    )
    assert RunContext.from_json(ctx.to_json()) == ctx


def test_expression_rejects_python_and_unknown_roots_are_validation_errors() -> None:
    with pytest.raises(TelescreenError):
        parse_expression("__import__('os')")
    invalid: str = VALID_YAML.replace('issue.severity == "high"', 'env.HOME == "x"')
    with pytest.raises(WorkflowValidationError) as caught:
        load_workflow(invalid, SourceFormat.YAML)
    assert any(issue.code == "invalid_expression" for issue in caught.value.issues)


def test_validation_reports_codes_and_json_pointer_paths() -> None:
    cases: tuple[tuple[str, str, str], ...] = (
        (
            VALID_YAML.replace("id: high", "id: branch"),
            "duplicate_id",
            "/steps/1/id",
        ),
        (
            VALID_YAML.replace("default: clean", "default: missing"),
            "unknown_step",
            "/steps/0/default",
        ),
        (
            VALID_YAML + "  - id: orphan\n    type: end\n    result: succeeded\n",
            "unreachable_step",
            "/steps/3/id",
        ),
        (
            """apiVersion: telescreen.workflow/v1
kind: Workflow
name: send-back
start: back
steps:
  - id: back
    type: send_back
    to: done
    reason: retry
  - id: done
    type: end
    result: succeeded
""",
            "invalid_send_back",
            "/steps/0/to",
        ),
        (
            """apiVersion: telescreen.workflow/v1
kind: Workflow
name: parallel
start: fan
steps:
  - id: fan
    type: parallel
    branches: [branch]
    join: joined
  - id: branch
    type: end
    result: succeeded
  - id: joined
    type: end
    result: succeeded
""",
            "invalid_parallel",
            "/steps/0/branches",
        ),
        (
            """apiVersion: telescreen.workflow/v1
kind: Workflow
name: empty
start: human
steps:
  - id: human
    type: human_task
    title: Triage
    assignee: team:security
    outcomes: {}
""",
            "empty_outcomes",
            "/steps/0/outcomes",
        ),
        (
            ALL_STEPS_YAML.replace("max_concurrency: 8", "max_concurrency: 0"),
            "invalid_value",
            "/steps/8/max_concurrency",
        ),
        (
            VALID_YAML.replace(
                "apiVersion: telescreen.workflow/v1", "apiVersion: telescreen.workflow/v2"
            ),
            "unsupported_api_version",
            "/apiVersion",
        ),
        (
            ALL_STEPS_YAML.replace(
                "prompt: 'Verify {{ issue.short_id }}'", "prompt: '{{ env.HOME }}'"
            ),
            "invalid_template",
            "/steps/1/prompt",
        ),
        (
            VALID_YAML.replace('issue.severity == "high"', 'env.HOME == "x"'),
            "invalid_expression",
            "/steps/0/cases/0/when",
        ),
        (
            """apiVersion: telescreen.workflow/v1
kind: Workflow
name: duration
start: delay
steps:
  - id: delay
    type: wait
    duration: not-a-duration
    next: done
  - id: done
    type: end
    result: succeeded
""",
            "invalid_duration",
            "/steps/0/duration",
        ),
    )
    for document, expected_code, expected_path in cases:
        with pytest.raises(WorkflowValidationError) as caught:
            load_workflow(document, SourceFormat.YAML)
        assert any(
            issue.code == expected_code and issue.path == expected_path
            for issue in caught.value.issues
        ), (expected_code, expected_path, tuple(caught.value.issues))


def test_validation_reports_multiple_issues_in_one_document() -> None:
    document: str = (
        VALID_YAML.replace("id: high", "id: branch").replace("default: clean", "default: absent")
        + "  - id: orphan\n    type: end\n    result: succeeded\n"
    )
    with pytest.raises(WorkflowValidationError) as caught:
        load_workflow(document, SourceFormat.YAML)
    observed: tuple[tuple[str, str], ...] = tuple(
        (issue.code, issue.path) for issue in caught.value.issues
    )
    assert ("duplicate_id", "/steps/1/id") in observed
    assert ("unknown_step", "/steps/0/default") in observed
    assert ("unreachable_step", "/steps/3/id") in observed


def test_map_inline_items_and_sub_workflow_are_rejected_as_unsupported() -> None:
    inline_items: str = ALL_STEPS_YAML.replace(
        "partition: {via: collector, collector: github}",
        "partition: {via: collector, collector: github, items: [one]}",
    )
    with pytest.raises(WorkflowValidationError) as inline_error:
        load_workflow(inline_items, SourceFormat.YAML)
    assert any(
        issue.code == "unsupported_field" and issue.path == "/steps/8/source/partition/items"
        for issue in inline_error.value.issues
    )
    sub_workflow_map: str = ALL_STEPS_YAML.replace(
        "map: {via: collector}", "map: {via: sub_workflow}"
    )
    with pytest.raises(WorkflowValidationError) as map_error:
        load_workflow(sub_workflow_map, SourceFormat.YAML)
    assert any(
        issue.code == "unsupported_field" and issue.path == "/steps/8/map/via"
        for issue in map_error.value.issues
    )


def test_invalid_retry_and_map_failure_fields_report_shape_errors() -> None:
    invalid_retry: str = VALID_YAML.replace(
        "start: branch", "start: branch\ndefaults: {retry: invalid}"
    )
    with pytest.raises(WorkflowValidationError) as retry_error:
        load_workflow(invalid_retry, SourceFormat.YAML)
    assert any(
        issue.code == "invalid_type" and issue.path == "/defaults/retry"
        for issue in retry_error.value.issues
    )
    invalid_ratio: str = ALL_STEPS_YAML.replace(
        "max_failed_ratio: 0.1", "max_failed_ratio: invalid"
    )
    with pytest.raises(WorkflowValidationError) as ratio_error:
        load_workflow(invalid_ratio, SourceFormat.YAML)
    assert any(
        issue.code == "invalid_type" and issue.path == "/steps/8/failure/max_failed_ratio"
        for issue in ratio_error.value.issues
    )
    negative_count: str = ALL_STEPS_YAML.replace("max_failed_items: 2", "max_failed_items: -1")
    with pytest.raises(WorkflowValidationError) as count_error:
        load_workflow(negative_count, SourceFormat.YAML)
    assert any(
        issue.code == "invalid_value" and issue.path == "/steps/8/failure/max_failed_items"
        for issue in count_error.value.issues
    )


def test_escalation_policy_round_trip_and_recipient_validation() -> None:
    text: str = """
name: secops
tiers:
  - after: PT4H
    assign_to: team:secops
    notify: [team:secops, email:security@example.invalid]
    channels: [slack, email]
"""
    policy: EscalationPolicySpec = load_escalation_policy(text, SourceFormat.YAML)
    assert escalation_from_json(escalation_to_json(policy)) == policy
    with pytest.raises(WorkflowValidationError):
        load_escalation_policy(text.replace("team:secops", "bob"), SourceFormat.YAML)


@pytest.mark.parametrize("duration", (None, "PT0S", "PT0.5S", "PT1.5S"))
def test_wait_duration_round_trips_exactly(duration: str | None) -> None:
    duration_field: str = f"    duration: {duration}\n" if duration is not None else ""
    document: str = f"""apiVersion: telescreen.workflow/v1
kind: Workflow
name: duration-round-trip
start: delay
steps:
  - id: delay
    type: wait
{duration_field}    next: done
  - id: done
    type: end
    result: succeeded
"""
    workflow: CompiledWorkflow = load_workflow(document, SourceFormat.YAML)
    assert compiled_from_json(compiled_to_json(workflow)) == workflow


def test_zero_human_task_sla_round_trips() -> None:
    document: str = ALL_STEPS_YAML.replace("sla: PT4H", "sla: PT0S")
    workflow: CompiledWorkflow = load_workflow(document, SourceFormat.YAML)
    assert compiled_from_json(compiled_to_json(workflow)) == workflow


def _condition_workflow_text(expression: str) -> str:
    """Build a minimal valid workflow containing one condition expression."""
    condition_source: str = json.dumps(expression)
    return f"""apiVersion: telescreen.workflow/v1
kind: Workflow
name: expression-round-trip
start: branch
steps:
  - id: branch
    type: condition
    cases:
      - when: {condition_source}
        next: done
    default: done
  - id: done
    type: end
    result: succeeded
"""


def _round_trip_context() -> RunContext:
    """Return masked values for round-trip expression evaluation."""
    return RunContext(
        IssueContext(
            1,
            "TELE-1",
            'say "hi"',
            "unresolved",
            "test",
            "credential",
            "high",
            "masked/location",
            1,
            "t1",
            "t1",
        ),
        RunInfo("run-1", "round-trip", "t0"),
        (StepResultContext("verify", "found", json_object((("score", json_float(0.5)),))),),
    )


@pytest.mark.parametrize(
    ("expression", "expected"),
    (
        ("""issue.title == 'say "hi"'""", True),
        (r'''issue.title == "say \"hi\""''', True),
        ("steps.verify.output.score > 0.00001", True),
        ("steps.verify.output.score > 1e20", False),
    ),
)
def test_expression_literals_round_trip_and_evaluate(expression: str, expected: bool) -> None:
    workflow: CompiledWorkflow = load_workflow(
        _condition_workflow_text(expression), SourceFormat.YAML
    )
    restored: CompiledWorkflow = compiled_from_json(compiled_to_json(workflow))
    assert restored == workflow
    original_step: StepSpec = workflow.step("branch")
    restored_step: StepSpec = restored.step("branch")
    assert isinstance(original_step, ConditionStep)
    assert isinstance(restored_step, ConditionStep)
    context: RunContext = _round_trip_context()
    original_result: EvalResult = evaluate(original_step.cases[0].when, context)
    restored_result: EvalResult = evaluate(restored_step.cases[0].when, context)
    assert restored_result == original_result
    assert original_result.value.as_bool() is expected


def test_malformed_expression_string_escapes_are_invalid_expressions() -> None:
    expressions: tuple[str, ...] = (
        r'issue.title == "\q"',
        r"issue.title == 'bad\q'",
    )
    for expression in expressions:
        with pytest.raises(WorkflowValidationError) as caught:
            load_workflow(_condition_workflow_text(expression), SourceFormat.YAML)
        assert any(
            issue.code == "invalid_expression" and issue.path == "/steps/0/cases/0/when"
            for issue in caught.value.issues
        )


def test_escalation_zero_and_fractional_durations_round_trip() -> None:
    text: str = """name: immediate
tiers:
  - after: PT0S
    assign_to: team:security
    notify: [team:security]
    channels: [slack]
  - after: PT90.5S
    assign_to: team:security
    notify: [team:security]
    channels: [email]
"""
    policy: EscalationPolicySpec = load_escalation_policy(text, SourceFormat.YAML)
    assert escalation_from_json(escalation_to_json(policy)) == policy


def test_published_schema_is_draft_2020_12_and_accepts_workflow() -> None:
    import jsonschema

    jsonschema.Draft202012Validator.check_schema(json.loads(workflow_schema_text()))
    jsonschema.validate(
        json.loads(
            json.dumps(
                {
                    "apiVersion": "telescreen.workflow/v1",
                    "kind": "Workflow",
                    "name": "triage",
                    "start": "done",
                    "steps": [{"id": "done", "type": "end", "result": "succeeded"}],
                }
            )
        ),
        json.loads(workflow_schema_text()),
    )
