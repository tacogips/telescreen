"""Load, validate and serialize workflow definitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from telescreen.core.durations import parse_duration
from telescreen.core.errors import TelescreenError
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
    parse_json,
    parse_yaml,
)
from telescreen.core.vocabulary import (
    DetectorKind,
    ErrorCode,
    IssueEventKind,
    NotificationChannel,
    RuleClass,
    RunStatus,
    Severity,
    SourceFormat,
    StepType,
)
from telescreen.workflow.dsl._iso import format_duration
from telescreen.workflow.dsl.expressions import parse_expression
from telescreen.workflow.dsl.model import (
    AgentProvider,
    AiAgentStep,
    CompiledWorkflow,
    ConditionCase,
    ConditionStep,
    Defaults,
    DetectStep,
    EndStep,
    EvidenceUploadStep,
    Expression,
    HumanTaskStep,
    JoinMode,
    JoinStep,
    MapStep,
    MapVia,
    NamedRoute,
    NamedTemplate,
    NotifyStep,
    Outcomes3,
    OutputField,
    OutputType,
    ParallelStep,
    PoliceVerifyStep,
    RetrySpec,
    SendBackStep,
    StepSpec,
    SubWorkflowStep,
    Template,
    TriggerMatch,
    TriggerSpec,
    WaitStep,
)
from telescreen.workflow.dsl.templates import parse_template
from telescreen.workflow.dsl.validate import ValidationIssue, validate_workflow


class WorkflowValidationError(TelescreenError):
    """Workflow parse or static validation failure without source values."""

    issues: tuple[ValidationIssue, ...]

    def __init__(self, issues: tuple[ValidationIssue, ...]) -> None:
        """Create an error containing only paths and codes."""
        self.issues = issues
        super().__init__(ErrorCode.WORKFLOW_INVALID, "workflow definition is invalid")


@dataclass
class _Reader:
    """Typed access to a JSON value while collecting safe shape issues."""

    issues: list[ValidationIssue]

    def string(self, value: JsonValue | None, path: str, required: bool = True) -> str:
        text: str | None = value.as_str() if value is not None else None
        if text is None:
            if required:
                self.issues.append(
                    ValidationIssue(path, "missing_field" if value is None else "invalid_type")
                )
            return ""
        return text

    def integer(self, value: JsonValue | None, path: str, default: int) -> int:
        number: int | None = value.as_int() if value is not None else None
        if value is None:
            return default
        if number is None:
            self.issues.append(ValidationIssue(path, "invalid_type"))
            return default
        return number

    def strings(self, value: JsonValue | None, path: str) -> tuple[str, ...]:
        if value is None:
            return ()
        if value.kind is not JsonKind.ARRAY:
            self.issues.append(ValidationIssue(path, "invalid_type"))
            return ()
        result: list[str] = []
        for index, item in enumerate(value.items):
            text: str | None = item.as_str()
            if text is None:
                self.issues.append(ValidationIssue(f"{path}/{index}", "invalid_type"))
            else:
                result.append(text)
        return tuple(result)


def load_workflow(text: str, source_format: SourceFormat) -> CompiledWorkflow:
    """Parse a JSON or YAML document, build the model, and validate all static rules."""
    try:
        document: JsonValue = (
            parse_yaml(text) if source_format is SourceFormat.YAML else parse_json(text)
        )
    except TelescreenError:
        raise WorkflowValidationError((ValidationIssue("", "invalid_document"),)) from None
    reader: _Reader = _Reader([])
    if document.kind is not JsonKind.OBJECT:
        raise WorkflowValidationError((ValidationIssue("", "invalid_document"),))
    api_version: str = reader.string(document.get("apiVersion"), "/apiVersion")
    kind: str = reader.string(document.get("kind"), "/kind")
    if api_version and api_version != "telescreen.workflow/v1":
        reader.issues.append(ValidationIssue("/apiVersion", "unsupported_api_version"))
    if kind and kind != "Workflow":
        reader.issues.append(ValidationIssue("/kind", "invalid_value"))
    name: str = reader.string(document.get("name"), "/name")
    description: str = reader.string(document.get("description"), "/description", False)
    start: str = reader.string(document.get("start"), "/start")
    trigger: TriggerSpec | None = _read_trigger(reader, document.get("trigger"))
    defaults: Defaults = _read_defaults(reader, document.get("defaults"))
    steps_value: JsonValue | None = document.get("steps")
    steps: tuple[StepSpec, ...] = _read_steps(reader, steps_value)
    if reader.issues:
        raise WorkflowValidationError(tuple(reader.issues))
    workflow: CompiledWorkflow = CompiledWorkflow(
        name, description, trigger, defaults, start, steps
    )
    found: list[ValidationIssue] = validate_workflow(workflow)
    if found:
        raise WorkflowValidationError(tuple(found))
    return workflow


def _read_trigger(reader: _Reader, value: JsonValue | None) -> TriggerSpec | None:
    if value is None or value.is_null():
        return None
    if value.kind is not JsonKind.OBJECT:
        reader.issues.append(ValidationIssue("/trigger", "invalid_type"))
        return None
    on_values: tuple[str, ...] = reader.strings(value.get("on"), "/trigger/on")
    events: list[IssueEventKind] = []
    for index, raw in enumerate(on_values):
        try:
            events.append(IssueEventKind(raw))
        except ValueError:
            reader.issues.append(ValidationIssue(f"/trigger/on/{index}", "invalid_value"))
    match_value: JsonValue | None = value.get("match")
    match: TriggerMatch = TriggerMatch()
    if match_value is not None and match_value.kind is JsonKind.OBJECT:
        raw_rules: tuple[str, ...] = reader.strings(
            match_value.get("rule_classes"), "/trigger/match/rule_classes"
        )
        rules: list[RuleClass] = []
        for index, raw in enumerate(raw_rules):
            try:
                rules.append(RuleClass(raw))
            except ValueError:
                reader.issues.append(
                    ValidationIssue(f"/trigger/match/rule_classes/{index}", "invalid_value")
                )
        severity_raw: str = reader.string(
            match_value.get("min_severity"), "/trigger/match/min_severity", False
        )
        severity: Severity | None = None
        if severity_raw:
            try:
                severity = Severity(severity_raw)
            except ValueError:
                reader.issues.append(
                    ValidationIssue("/trigger/match/min_severity", "invalid_value")
                )
        match = TriggerMatch(
            reader.strings(match_value.get("sources"), "/trigger/match/sources"),
            tuple(rules),
            severity,
        )
    return TriggerSpec(tuple(events), match)


def _read_defaults(reader: _Reader, value: JsonValue | None) -> Defaults:
    if value is None:
        return Defaults()
    if value.kind is not JsonKind.OBJECT:
        reader.issues.append(ValidationIssue("/defaults", "invalid_type"))
        return Defaults()
    visits: int = reader.integer(value.get("max_step_visits"), "/defaults/max_step_visits", 20)
    retry: RetrySpec | None = _read_retry(reader, value.get("retry"), "/defaults/retry")
    return Defaults(visits, retry)


def _read_retry(reader: _Reader, value: JsonValue | None, path: str) -> RetrySpec | None:
    if value is None or value.is_null():
        return None
    if value.kind is not JsonKind.OBJECT:
        reader.issues.append(ValidationIssue(path, "invalid_type"))
        return None
    attempts: int = reader.integer(
        value.get("max_attempts"),
        f"{path}/max_attempts",
        3,
    )
    backoff: timedelta = _duration(
        reader,
        value.get("backoff") if value.kind is JsonKind.OBJECT else None,
        f"{path}/backoff",
        timedelta(seconds=10),
    )
    return RetrySpec(attempts, backoff)


def _read_steps(reader: _Reader, value: JsonValue | None) -> tuple[StepSpec, ...]:
    if value is None or value.kind is not JsonKind.ARRAY:
        reader.issues.append(
            ValidationIssue("/steps", "missing_field" if value is None else "invalid_type")
        )
        return ()
    steps: list[StepSpec] = []
    for index, item in enumerate(value.items):
        path: str = f"/steps/{index}"
        if item.kind is not JsonKind.OBJECT:
            reader.issues.append(ValidationIssue(path, "invalid_type"))
            continue
        steps.append(_read_step(reader, item, path))
    return tuple(steps)


def _read_step(reader: _Reader, value: JsonValue, path: str) -> StepSpec:
    step_id: str = reader.string(value.get("id"), f"{path}/id")
    kind_text: str = reader.string(value.get("type"), f"{path}/type")
    common: tuple[str, str | None, str | None, RetrySpec | None, str | None] = (
        step_id,
        reader.string(value.get("name"), f"{path}/name", False) or None,
        reader.string(value.get("next"), f"{path}/next", False) or None,
        _read_retry(reader, value.get("retry"), f"{path}/retry"),
        reader.string(value.get("on_error"), f"{path}/on_error", False) or None,
    )
    try:
        step_type: StepType = StepType(kind_text)
    except ValueError:
        reader.issues.append(ValidationIssue(f"{path}/type", "invalid_value"))
        return EndStep(step_id)
    if step_type is StepType.MAP:
        source: JsonValue | None = value.get("source")
        partition: JsonValue | None = source.get("partition") if source is not None else None
        mapping: JsonValue | None = value.get("map")
        if source is not None and source.get("items") is not None:
            reader.issues.append(ValidationIssue(f"{path}/source/items", "unsupported_field"))
        if partition is not None and partition.get("items") is not None:
            reader.issues.append(
                ValidationIssue(f"{path}/source/partition/items", "unsupported_field")
            )
        for nested, nested_path in (
            (partition, f"{path}/source/partition"),
            (mapping, f"{path}/map"),
        ):
            via_value: JsonValue | None = nested.get("via") if nested is not None else None
            if via_value is not None and via_value.as_str() == "sub_workflow":
                reader.issues.append(ValidationIssue(f"{nested_path}/via", "unsupported_field"))
    if step_type is StepType.DETECT:
        return DetectStep(
            *common,
            detectors=_enum_tuple(
                reader, value.get("detectors"), DetectorKind, f"{path}/detectors"
            ),
            outcomes=_outcomes(reader, value.get("outcomes"), path),
        )
    if step_type is StepType.AI_AGENT:
        provider: AgentProvider = _enum(
            reader, value.get("provider"), AgentProvider, f"{path}/provider", AgentProvider.MANAGED
        )
        prompt: Template = _template(reader, value.get("prompt"), f"{path}/prompt")
        return AiAgentStep(
            *common,
            provider=provider,
            model=reader.string(value.get("model"), f"{path}/model", False) or None,
            prompt=prompt,
            output=_read_output(reader, value.get("output"), path),
        )
    if step_type is StepType.CONDITION:
        cases: list[ConditionCase] = []
        case_values: JsonValue | None = value.get("cases")
        if case_values is not None and case_values.kind is JsonKind.ARRAY:
            for case_index, case in enumerate(case_values.items):
                case_path: str = f"{path}/cases/{case_index}"
                expr: str = reader.string(
                    case.get("when") if case.kind is JsonKind.OBJECT else None, f"{case_path}/when"
                )
                destination: str = reader.string(
                    case.get("next") if case.kind is JsonKind.OBJECT else None, f"{case_path}/next"
                )
                try:
                    cases.append(ConditionCase(parse_expression(expr), destination))
                except TelescreenError:
                    reader.issues.append(ValidationIssue(f"{case_path}/when", "invalid_expression"))
        return ConditionStep(
            *common,
            cases=tuple(cases),
            default=reader.string(value.get("default"), f"{path}/default"),
        )
    if step_type is StepType.HUMAN_TASK:
        return HumanTaskStep(
            *common,
            title=_template(reader, value.get("title"), f"{path}/title"),
            assignee=reader.string(value.get("assignee"), f"{path}/assignee"),
            outcomes=_routes(reader, value.get("outcomes"), f"{path}/outcomes"),
            sla=_optional_duration(reader, value.get("sla"), f"{path}/sla"),
            escalation_policy=reader.string(
                value.get("escalation_policy"), f"{path}/escalation_policy", False
            )
            or None,
            notify_on_assign=_boolean(
                reader, value.get("notify_on_assign"), True, f"{path}/notify_on_assign"
            ),
        )
    if step_type is StepType.NOTIFY:
        channel: NotificationChannel = _enum(
            reader,
            value.get("channel"),
            NotificationChannel,
            f"{path}/channel",
            NotificationChannel.EMAIL,
        )
        return NotifyStep(
            *common,
            channel=channel,
            to=reader.strings(value.get("to"), f"{path}/to"),
            subject=_template(reader, value.get("subject"), f"{path}/subject"),
            body=_template(reader, value.get("body"), f"{path}/body"),
        )
    if step_type is StepType.EVIDENCE_UPLOAD:
        return EvidenceUploadStep(
            *common,
            title=_template(reader, value.get("title"), f"{path}/title"),
            assignee=reader.string(value.get("assignee"), f"{path}/assignee"),
            min_files=reader.integer(value.get("min_files"), f"{path}/min_files", 1),
            sla=_optional_duration(reader, value.get("sla"), f"{path}/sla"),
            escalation_policy=reader.string(
                value.get("escalation_policy"), f"{path}/escalation_policy", False
            )
            or None,
        )
    if step_type is StepType.POLICE_VERIFY:
        return PoliceVerifyStep(
            *common,
            task_type=reader.string(value.get("task_type"), f"{path}/task_type"),
            labels=reader.strings(value.get("labels"), f"{path}/labels"),
            input=_named_templates(reader, value.get("input"), f"{path}/input"),
            timeout=_duration(reader, value.get("timeout"), f"{path}/timeout", timedelta(hours=1)),
            outcomes=_outcomes(reader, value.get("outcomes"), path),
        )
    if step_type is StepType.WAIT:
        return WaitStep(
            *common,
            duration=_duration(reader, value.get("duration"), f"{path}/duration", timedelta(0)),
        )
    if step_type is StepType.SEND_BACK:
        return SendBackStep(
            *common,
            to=reader.string(value.get("to"), f"{path}/to"),
            reason=_template(reader, value.get("reason"), f"{path}/reason"),
            max_send_backs=reader.integer(value.get("max_send_backs"), f"{path}/max_send_backs", 3),
            on_exhausted=reader.string(value.get("on_exhausted"), f"{path}/on_exhausted", False)
            or None,
        )
    if step_type is StepType.PARALLEL:
        return ParallelStep(
            *common,
            branches=reader.strings(value.get("branches"), f"{path}/branches"),
            join=reader.string(value.get("join"), f"{path}/join"),
        )
    if step_type is StepType.JOIN:
        return JoinStep(
            *common, mode=_enum(reader, value.get("mode"), JoinMode, f"{path}/mode", JoinMode.ALL)
        )
    if step_type is StepType.SUB_WORKFLOW:
        version_json: JsonValue | None = value.get("version")
        version_value: int | None = version_json.as_int() if version_json is not None else None
        return SubWorkflowStep(
            *common,
            workflow=reader.string(value.get("workflow"), f"{path}/workflow"),
            version=version_value,
            outcomes=_outcomes(reader, value.get("outcomes"), path),
        )
    if step_type is StepType.MAP:
        return _read_map(reader, value, path, common)
    return EndStep(
        *common,
        result=_enum(reader, value.get("result"), RunStatus, f"{path}/result", RunStatus.SUCCEEDED),
    )


def _read_map(
    reader: _Reader,
    value: JsonValue,
    path: str,
    common: tuple[str, str | None, str | None, RetrySpec | None, str | None],
) -> MapStep:
    source: JsonValue | None = value.get("source")
    partition: JsonValue | None = source.get("partition") if source is not None else None
    mapping: JsonValue | None = value.get("map")
    partition_via: MapVia = _enum(
        reader,
        partition.get("via") if partition is not None else None,
        MapVia,
        f"{path}/source/partition/via",
        MapVia.COLLECTOR,
    )
    map_via: MapVia = _enum(
        reader,
        mapping.get("via") if mapping is not None else None,
        MapVia,
        f"{path}/map/via",
        MapVia.COLLECTOR,
    )
    failure: JsonValue | None = value.get("failure")
    if failure is not None and failure.kind is not JsonKind.OBJECT:
        reader.issues.append(ValidationIssue(f"{path}/failure", "invalid_type"))
    ratio: JsonValue | None = failure.get("max_failed_ratio") if failure is not None else None
    ratio_value: float | None = ratio.as_float() if ratio is not None else None
    ratio_number: float = ratio_value if ratio_value is not None else 0.0
    if ratio is not None and ratio_value is None:
        reader.issues.append(ValidationIssue(f"{path}/failure/max_failed_ratio", "invalid_type"))
    return MapStep(
        *common,
        partition_via=partition_via,
        collector=reader.string(
            partition.get("collector") if partition is not None else None,
            f"{path}/source/partition/collector",
            partition_via is MapVia.COLLECTOR,
        ),
        partition_task_type=reader.string(
            partition.get("task_type") if partition is not None else None,
            f"{path}/source/partition/task_type",
            False,
        )
        or None,
        partition_labels=reader.strings(
            partition.get("labels") if partition is not None else None,
            f"{path}/source/partition/labels",
        ),
        map_via=map_via,
        map_task_type=reader.string(
            mapping.get("task_type") if mapping is not None else None,
            f"{path}/map/task_type",
            False,
        )
        or None,
        map_labels=reader.strings(
            mapping.get("labels") if mapping is not None else None, f"{path}/map/labels"
        ),
        max_concurrency=reader.integer(value.get("max_concurrency"), f"{path}/max_concurrency", 16),
        shard_timeout=_duration(
            reader, value.get("shard_timeout"), f"{path}/shard_timeout", timedelta(minutes=5)
        ),
        shard_max_attempts=reader.integer(
            value.get("shard_max_attempts"), f"{path}/shard_max_attempts", 3
        ),
        max_failed_ratio=ratio_number,
        max_failed_items=reader.integer(
            failure.get("max_failed_items") if failure is not None else None,
            f"{path}/failure/max_failed_items",
            0,
        ),
        timeout=_duration(reader, value.get("timeout"), f"{path}/timeout", timedelta(hours=1)),
        outcomes=_outcomes(reader, value.get("outcomes"), path),
    )


def _outcomes(reader: _Reader, value: JsonValue | None, path: str) -> Outcomes3:
    names: tuple[str, ...] = ("found", "clean", "error", "passed", "failed", "partial", "succeeded")
    routes: list[tuple[str, str | None]] = []
    for name in names:
        raw: str = (
            reader.string(value.get(name), f"{path}/outcomes/{name}", False)
            if value is not None and value.kind is JsonKind.OBJECT
            else ""
        )
        routes.append((name, raw or None))
    return Outcomes3(
        found=routes[0][1],
        clean=routes[1][1],
        error=routes[2][1],
        passed=routes[3][1],
        failed=routes[4][1],
        partial=routes[5][1],
        succeeded=routes[6][1],
    )


def _routes(reader: _Reader, value: JsonValue | None, path: str) -> tuple[NamedRoute, ...]:
    if value is None or value.kind is not JsonKind.OBJECT:
        reader.issues.append(
            ValidationIssue(path, "missing_field" if value is None else "invalid_type")
        )
        return ()
    return tuple(
        NamedRoute(member.name, reader.string(member.value, f"{path}/{member.name}"))
        for member in value.members
    )


def _named_templates(
    reader: _Reader, value: JsonValue | None, path: str
) -> tuple[NamedTemplate, ...]:
    if value is None or value.kind is not JsonKind.OBJECT:
        reader.issues.append(
            ValidationIssue(path, "missing_field" if value is None else "invalid_type")
        )
        return ()
    result: list[NamedTemplate] = []
    for member in value.members:
        text: str = reader.string(member.value, f"{path}/{member.name}")
        result.append(
            NamedTemplate(member.name, _parse_template(reader, text, f"{path}/{member.name}"))
        )
    return tuple(result)


def _read_output(reader: _Reader, value: JsonValue | None, path: str) -> tuple[OutputField, ...]:
    if value is None or value.kind is not JsonKind.ARRAY:
        reader.issues.append(
            ValidationIssue(f"{path}/output", "missing_field" if value is None else "invalid_type")
        )
        return ()
    result: list[OutputField] = []
    for index, item in enumerate(value.items):
        field_path: str = f"{path}/output/{index}"
        raw_type: str = reader.string(
            item.get("type") if item.kind is JsonKind.OBJECT else None, f"{field_path}/type"
        )
        try:
            output_type: OutputType = OutputType(raw_type)
        except ValueError:
            output_type = OutputType.STRING
            reader.issues.append(ValidationIssue(f"{field_path}/type", "invalid_value"))
        result.append(
            OutputField(
                reader.string(
                    item.get("name") if item.kind is JsonKind.OBJECT else None, f"{field_path}/name"
                ),
                output_type,
                reader.strings(
                    item.get("values") if item.kind is JsonKind.OBJECT else None,
                    f"{field_path}/values",
                ),
            )
        )
    return tuple(result)


def _template(reader: _Reader, value: JsonValue | None, path: str) -> Template:
    text: str = reader.string(value, path)
    return _parse_template(reader, text, path)


def _parse_template(reader: _Reader, text: str, path: str) -> Template:
    try:
        return parse_template(text)
    except TelescreenError:
        reader.issues.append(ValidationIssue(path, "invalid_template"))
        return ()


def _duration(reader: _Reader, value: JsonValue | None, path: str, default: timedelta) -> timedelta:
    if value is None:
        return default
    text: str | None = value.as_str()
    if text is None:
        reader.issues.append(ValidationIssue(path, "invalid_type"))
        return default
    try:
        return parse_duration(text)
    except TelescreenError:
        reader.issues.append(ValidationIssue(path, "invalid_duration"))
        return default


def _optional_duration(reader: _Reader, value: JsonValue | None, path: str) -> timedelta | None:
    return (
        None if value is None or value.is_null() else _duration(reader, value, path, timedelta(0))
    )


def _boolean(reader: _Reader, value: JsonValue | None, default: bool, path: str) -> bool:
    if value is None:
        return default
    boolean: bool | None = value.as_bool()
    if boolean is None:
        reader.issues.append(ValidationIssue(path, "invalid_type"))
        return default
    return boolean


def _enum[T: StrEnum](
    reader: _Reader, value: JsonValue | None, enum: type[T], path: str, default: T
) -> T:
    raw: str = reader.string(value, path, False)
    try:
        return enum(raw) if raw else default
    except ValueError:
        reader.issues.append(ValidationIssue(path, "invalid_value"))
        return default


def _enum_tuple[T: StrEnum](
    reader: _Reader, value: JsonValue | None, enum: type[T], path: str
) -> tuple[T, ...] | None:
    if value is None:
        return None
    values: list[T] = []
    for index, raw in enumerate(reader.strings(value, path)):
        try:
            values.append(enum(raw))
        except ValueError:
            reader.issues.append(ValidationIssue(f"{path}/{index}", "invalid_value"))
    return tuple(values)


def compiled_to_json(workflow: CompiledWorkflow) -> JsonValue:
    """Serialize a compiled workflow into canonical-storage-safe JSON values."""
    members: list[tuple[str, JsonValue]] = [
        ("apiVersion", json_str("telescreen.workflow/v1")),
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
    return json_object(members)


def compiled_from_json(value: JsonValue) -> CompiledWorkflow:
    """Restore a compiled workflow from its stored JSON representation."""
    if value.kind is not JsonKind.OBJECT:
        raise WorkflowValidationError((ValidationIssue("", "invalid_document"),))
    from telescreen.core.json import dump_json

    return load_workflow(dump_json(value), SourceFormat.JSON)


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
        _append_optional_duration(members, "sla", step.sla)
        _append_optional_string(members, "escalation_policy", step.escalation_policy)
    elif isinstance(step, NotifyStep):
        members.extend(
            (
                ("channel", json_str(step.channel.value)),
                ("to", json_array(tuple(json_str(item) for item in step.to))),
                ("subject", json_str(_template_text(step.subject))),
                ("body", json_str(_template_text(step.body))),
            )
        )
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
