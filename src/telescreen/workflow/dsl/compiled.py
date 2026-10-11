"""Canonical JSON serialization for compiled workflow definitions."""

from __future__ import annotations

from datetime import timedelta

from telescreen.core.json import (
    JsonKind,
    JsonValue,
    dump_json,
    json_array,
    json_bool,
    json_float,
    json_int,
    json_null,
    json_object,
    json_str,
)
from telescreen.core.vocabulary import HumanTaskKind, SourceFormat
from telescreen.workflow.dsl._iso import format_duration
from telescreen.workflow.dsl.model import (
    AiAgentStep,
    CompiledWorkflow,
    ConditionStep,
    Defaults,
    DetectStep,
    EndStep,
    EvidenceUploadStep,
    Expression,
    HumanTaskStep,
    JoinStep,
    MapStep,
    NotificationTargetSpec,
    NotifyStep,
    Outcomes3,
    ParallelStep,
    PoliceVerifyStep,
    RetrySpec,
    SendBackStep,
    StepSpec,
    SubWorkflowStep,
    Template,
    TriggerSpec,
    WaitStep,
)
from telescreen.workflow.dsl.notification_sets import (
    notification_sets_json,
    resolve_set_targets,
    targets_json,
)


def compiled_to_json(workflow: CompiledWorkflow) -> JsonValue:
    """Serialize a compiled workflow into canonical-storage-safe JSON values."""
    members: list[tuple[str, JsonValue]] = [
        ("apiVersion", json_str(workflow.api_version)),
        ("kind", json_str("Workflow")),
        ("name", json_str(workflow.name)),
        ("start", json_str(workflow.start)),
        ("steps", json_array(tuple(_step_json(step) for step in workflow.steps))),
    ]
    if workflow.description is not None:
        members.append(("description", json_str(workflow.description)))
    members.append(("defaults", _defaults_json(workflow.defaults)))
    if workflow.trigger is not None:
        members.append(("trigger", _trigger_json(workflow.trigger)))
    if workflow.notification_sets:
        members.append(("notification_sets", notification_sets_json(workflow.notification_sets)))
    return json_object(members)


def compiled_from_json(value: JsonValue) -> CompiledWorkflow:
    """Restore a compiled workflow from its stored JSON representation.

    Step ``set_targets`` are rebuilt from the stored notification sets, because the engine
    executes this decoded form rather than the loader output.
    """
    from telescreen.workflow.dsl.loader import (
        ValidationIssue,
        WorkflowValidationError,
        load_workflow,
    )

    if value.kind is not JsonKind.OBJECT:
        raise WorkflowValidationError((ValidationIssue("", "invalid_document"),))
    return resolve_set_targets(load_workflow(dump_json(value), SourceFormat.JSON))


def _retry_json(retry: RetrySpec | None) -> JsonValue:
    if retry is None:
        return json_null()
    return json_object(
        (
            ("max_attempts", json_int(retry.max_attempts)),
            ("backoff", json_str(format_duration(retry.backoff))),
        )
    )


def _defaults_json(defaults: Defaults) -> JsonValue:
    return json_object(
        (
            ("max_step_visits", json_int(defaults.max_step_visits)),
            ("retry", _retry_json(defaults.retry)),
        )
    )


def _trigger_json(trigger: TriggerSpec) -> JsonValue:
    match: JsonValue = json_object(
        (
            ("sources", json_array(tuple(json_str(item) for item in trigger.match.sources))),
            (
                "rule_classes",
                json_array(tuple(json_str(item.value) for item in trigger.match.rule_classes)),
            ),
            (
                "min_severity",
                json_str(trigger.match.min_severity.value)
                if trigger.match.min_severity is not None
                else json_null(),
            ),
        )
    )
    return json_object(
        (
            ("on", json_array(tuple(json_str(item.value) for item in trigger.on))),
            ("match", match),
        )
    )


def _template_text(template: Template) -> str:
    return "".join(
        part.literal if part.literal is not None else "{{ " + ".".join(part.path) + " }}"
        for part in template
    )


def _outcomes_json(outcomes: Outcomes3) -> JsonValue:
    pairs: tuple[tuple[str, str | None], ...] = (
        ("found", outcomes.found),
        ("clean", outcomes.clean),
        ("error", outcomes.error),
        ("passed", outcomes.passed),
        ("failed", outcomes.failed),
        ("succeeded", outcomes.succeeded),
        ("partial", outcomes.partial),
    )
    return json_object(
        tuple((key, json_str(value) if value is not None else json_null()) for key, value in pairs)
    )


def _step_json(step: StepSpec) -> JsonValue:
    members: list[tuple[str, JsonValue]] = [
        ("id", json_str(step.id)),
        ("type", json_str(step.step_type.value)),
    ]
    for key, value in (("name", step.name), ("next", step.next), ("on_error", step.on_error)):
        if value is not None:
            members.append((key, json_str(value)))
    if step.retry is not None:
        members.append(("retry", _retry_json(step.retry)))
    if isinstance(step, DetectStep):
        if step.detectors is not None:
            members.append(
                ("detectors", json_array(tuple(json_str(item.value) for item in step.detectors)))
            )
        members.append(("outcomes", _outcomes_json(step.outcomes)))
    elif isinstance(step, AiAgentStep):
        members.extend(
            (
                ("provider", json_str(step.provider.value)),
                ("prompt", json_str(_template_text(step.prompt))),
            )
        )
        if step.model is not None:
            members.append(("model", json_str(step.model)))
        members.append(
            (
                "output",
                json_array(
                    tuple(
                        json_object(
                            (
                                ("name", json_str(item.name)),
                                ("type", json_str(item.type.value)),
                                (
                                    "values",
                                    json_array(tuple(json_str(value) for value in item.values)),
                                ),
                            )
                        )
                        for item in step.output
                    )
                ),
            )
        )
    elif isinstance(step, ConditionStep):
        members.extend(
            (
                (
                    "cases",
                    json_array(
                        tuple(
                            json_object(
                                (
                                    ("when", json_str(_expression_text(case.when))),
                                    ("next", json_str(case.next)),
                                )
                            )
                            for case in step.cases
                        )
                    ),
                ),
                ("default", json_str(step.default)),
            )
        )
    elif isinstance(step, HumanTaskStep):
        members.extend(
            (
                ("title", json_str(_template_text(step.title))),
                ("assignee", json_str(step.assignee)),
                (
                    "outcomes",
                    json_object(tuple((route.name, json_str(route.to)) for route in step.outcomes)),
                ),
                ("notify_on_assign", json_bool(step.notify_on_assign)),
            )
        )
        if step.kind is HumanTaskKind.OWNER_REVIEW:
            members.append(("kind", json_str(step.kind.value)))
        _append_optional_duration(members, "sla", step.sla)
        _append_optional_string(members, "escalation_policy", step.escalation_policy)
        _append_notify_targets(members, step.notify_set, step.notify_targets)
    elif isinstance(step, NotifyStep):
        members.extend(
            (
                ("channel", json_str(step.channel.value)),
                ("to", json_array(tuple(json_str(item) for item in step.to))),
                ("subject", json_str(_template_text(step.subject))),
                ("body", json_str(_template_text(step.body))),
            )
        )
        _append_optional_string(members, "notification_set", step.notification_set)
        if step.targets:
            members.append(("targets", targets_json(step.targets)))
        _append_optional_string(members, "template", step.template)
    elif isinstance(step, EvidenceUploadStep):
        members.extend(
            (
                ("title", json_str(_template_text(step.title))),
                ("assignee", json_str(step.assignee)),
                ("min_files", json_int(step.min_files)),
            )
        )
        _append_optional_duration(members, "sla", step.sla)
        _append_optional_string(members, "escalation_policy", step.escalation_policy)
        _append_notify_targets(members, step.notify_set, step.notify_targets)
    elif isinstance(step, PoliceVerifyStep):
        members.extend(
            (
                ("task_type", json_str(step.task_type)),
                ("labels", json_array(tuple(json_str(item) for item in step.labels))),
                (
                    "input",
                    json_object(
                        tuple(
                            (item.name, json_str(_template_text(item.template)))
                            for item in step.input
                        )
                    ),
                ),
                ("timeout", json_str(format_duration(step.timeout))),
                ("outcomes", _outcomes_json(step.outcomes)),
            )
        )
    elif isinstance(step, WaitStep):
        members.append(("duration", json_str(format_duration(step.duration))))
    elif isinstance(step, SendBackStep):
        members.extend(
            (
                ("to", json_str(step.to)),
                ("reason", json_str(_template_text(step.reason))),
                ("max_send_backs", json_int(step.max_send_backs)),
            )
        )
        _append_optional_string(members, "on_exhausted", step.on_exhausted)
    elif isinstance(step, ParallelStep):
        members.extend(
            (
                ("branches", json_array(tuple(json_str(item) for item in step.branches))),
                ("join", json_str(step.join)),
            )
        )
    elif isinstance(step, JoinStep):
        members.append(("mode", json_str(step.mode.value)))
    elif isinstance(step, SubWorkflowStep):
        members.extend(
            (("workflow", json_str(step.workflow)), ("outcomes", _outcomes_json(step.outcomes)))
        )
        if step.version is not None:
            members.append(("version", json_int(step.version)))
    elif isinstance(step, MapStep):
        partition: JsonValue = json_object(
            (
                ("via", json_str(step.partition_via.value)),
                ("collector", json_str(step.collector)),
                ("task_type", json_str(step.partition_task_type or "")),
                ("labels", json_array(tuple(json_str(item) for item in step.partition_labels))),
            )
        )
        source: JsonValue = json_object((("partition", partition),))
        mapping: JsonValue = json_object(
            (
                ("via", json_str(step.map_via.value)),
                ("task_type", json_str(step.map_task_type or "")),
                ("labels", json_array(tuple(json_str(item) for item in step.map_labels))),
            )
        )
        failure: JsonValue = json_object(
            (
                ("max_failed_ratio", json_float_value(step.max_failed_ratio)),
                ("max_failed_items", json_int(step.max_failed_items)),
            )
        )
        members.extend(
            (
                ("source", source),
                ("map", mapping),
                ("max_concurrency", json_int(step.max_concurrency)),
                ("shard_timeout", json_str(format_duration(step.shard_timeout))),
                ("shard_max_attempts", json_int(step.shard_max_attempts)),
                ("failure", failure),
                ("timeout", json_str(format_duration(step.timeout))),
                ("outcomes", _outcomes_json(step.outcomes)),
            )
        )
    elif isinstance(step, EndStep):
        members.append(("result", json_str(step.result.value)))
    return json_object(members)


def _append_optional_string(
    members: list[tuple[str, JsonValue]], key: str, value: str | None
) -> None:
    if value is not None:
        members.append((key, json_str(value)))


def _append_notify_targets(
    members: list[tuple[str, JsonValue]],
    notify_set: str | None,
    notify_targets: tuple[NotificationTargetSpec, ...],
) -> None:
    _append_optional_string(members, "notify_set", notify_set)
    if notify_targets:
        members.append(("notify_targets", targets_json(notify_targets)))


def _append_optional_duration(
    members: list[tuple[str, JsonValue]], key: str, value: timedelta | None
) -> None:
    if value is not None:
        members.append((key, json_str(format_duration(value))))


def json_float_value(value: float) -> JsonValue:
    return json_float(value)


def _expression_text(expression: Expression) -> str:
    if expression.kind == "literal" and expression.value is not None:
        return dump_json(expression.value)
    if expression.kind == "path":
        return ".".join(expression.path)
    if expression.kind == "list":
        return "[" + ", ".join(_expression_text(item) for item in expression.children) + "]"
    if expression.kind == "unary":
        return "not (" + _expression_text(expression.children[0]) + ")"
    return (
        "("
        + _expression_text(expression.children[0])
        + " "
        + expression.path[0]
        + " "
        + _expression_text(expression.children[1])
        + ")"
    )
