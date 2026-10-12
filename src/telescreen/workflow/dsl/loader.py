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
    parse_json,
    parse_yaml,
)
from telescreen.core.vocabulary import (
    DetectorKind,
    ErrorCode,
    HumanTaskKind,
    IssueEventKind,
    NotificationChannel,
    RuleClass,
    RunStatus,
    Severity,
    SourceFormat,
    StepType,
)
from telescreen.workflow.dsl.compiled import (
    compiled_from_json as compiled_from_json,
)
from telescreen.workflow.dsl.compiled import (
    compiled_to_json as compiled_to_json,
)
from telescreen.workflow.dsl.compiled import (
    json_float_value as json_float_value,
)
from telescreen.workflow.dsl.expressions import parse_expression
from telescreen.workflow.dsl.model import (
    API_VERSION_V1,
    API_VERSION_V2,
    AgentProvider,
    AiAgentStep,
    CompiledWorkflow,
    ConditionCase,
    ConditionStep,
    Defaults,
    DetectStep,
    EndStep,
    EvidenceUploadStep,
    HumanTaskStep,
    JoinMode,
    JoinStep,
    MapStep,
    MapVia,
    NamedRoute,
    NamedTemplate,
    NotificationSetSpec,
    NotificationTargetSpec,
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
from telescreen.workflow.dsl.notification_sets import (
    V2_ONLY_STEP_FIELDS,
    V2_ONLY_TOP_LEVEL,
    channel_only_issues,
    read_notification_sets,
    read_targets,
    read_template_name,
    resolve_set_targets,
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


def load_new_workflow(text: str, source_format: SourceFormat) -> CompiledWorkflow:
    """Load a newly submitted source and apply rules that only new definitions must meet.

    ``slack_channel:`` recipients must be channel ids here; ``load_workflow`` keeps accepting
    stored definitions written before that rule so they still list, activate and run.
    """
    workflow: CompiledWorkflow = load_workflow(text, source_format)
    issues: list[ValidationIssue] = channel_only_issues(workflow)
    if issues:
        raise WorkflowValidationError(tuple(issues))
    return workflow


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
    if api_version and api_version not in (API_VERSION_V1, API_VERSION_V2):
        reader.issues.append(ValidationIssue("/apiVersion", "unsupported_api_version"))
    v2: bool = api_version == API_VERSION_V2
    if not v2:
        _reject_v2_fields(reader, document)
    if kind and kind != "Workflow":
        reader.issues.append(ValidationIssue("/kind", "invalid_value"))
    name: str = reader.string(document.get("name"), "/name")
    description: str = reader.string(document.get("description"), "/description", False)
    start: str = reader.string(document.get("start"), "/start")
    trigger: TriggerSpec | None = _read_trigger(reader, document.get("trigger"))
    defaults: Defaults = _read_defaults(reader, document.get("defaults"))
    steps_value: JsonValue | None = document.get("steps")
    steps: tuple[StepSpec, ...] = _read_steps(reader, steps_value, v2)
    notification_sets: tuple[NotificationSetSpec, ...] = (
        read_notification_sets(
            reader.issues, document.get("notification_sets"), "/notification_sets"
        )
        if v2
        else ()
    )
    if reader.issues:
        raise WorkflowValidationError(tuple(reader.issues))
    workflow: CompiledWorkflow = resolve_set_targets(
        CompiledWorkflow(
            name,
            description,
            trigger,
            defaults,
            start,
            steps,
            API_VERSION_V2 if v2 else API_VERSION_V1,
            notification_sets,
        )
    )
    found: list[ValidationIssue] = validate_workflow(workflow)
    if found:
        raise WorkflowValidationError(tuple(found))
    return workflow


def _reject_v2_fields(reader: _Reader, document: JsonValue) -> None:
    """Report DSL v2 fields that appear in a v1 document."""
    for key in V2_ONLY_TOP_LEVEL:
        if document.get(key) is not None:
            reader.issues.append(ValidationIssue(f"/{key}", "requires_api_version_v2"))
    steps_value: JsonValue | None = document.get("steps")
    if steps_value is None or steps_value.kind is not JsonKind.ARRAY:
        return
    for index, item in enumerate(steps_value.items):
        for key in V2_ONLY_STEP_FIELDS:
            if item.kind is JsonKind.OBJECT and item.get(key) is not None:
                reader.issues.append(
                    ValidationIssue(f"/steps/{index}/{key}", "requires_api_version_v2")
                )


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


def _read_steps(reader: _Reader, value: JsonValue | None, v2: bool) -> tuple[StepSpec, ...]:
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
        steps.append(_read_step(reader, item, path, v2))
    return tuple(steps)


def _read_step(reader: _Reader, value: JsonValue, path: str, v2: bool = False) -> StepSpec:
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
        task_kind_text: str = reader.string(value.get("kind"), f"{path}/kind", False)
        task_kind: HumanTaskKind
        if task_kind_text not in ("", "approval", "owner_review"):
            reader.issues.append(ValidationIssue(f"{path}/kind", "invalid_value"))
            task_kind = HumanTaskKind.APPROVAL
        else:
            task_kind = HumanTaskKind(task_kind_text or "approval")
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
            kind=task_kind,
            notify_set=_notify_set(reader, value, path),
            notify_targets=_notify_targets(reader, value, path),
        )
    if step_type is StepType.NOTIFY:
        return _read_notify(reader, value, path, common, v2)
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
            notify_set=_notify_set(reader, value, path),
            notify_targets=_notify_targets(reader, value, path),
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


def _read_notify(
    reader: _Reader,
    value: JsonValue,
    path: str,
    common: tuple[str, str | None, str | None, RetrySpec | None, str | None],
    v2: bool,
) -> NotifyStep:
    """Read a notify step: the v1 ``to`` form, or (v2) a set reference or inline targets."""
    channel: NotificationChannel = _enum(
        reader,
        value.get("channel"),
        NotificationChannel,
        f"{path}/channel",
        NotificationChannel.EMAIL,
    )
    to: tuple[str, ...] = reader.strings(value.get("to"), f"{path}/to")
    set_name: str | None = (
        reader.string(value.get("notification_set"), f"{path}/notification_set", False) or None
    )
    targets_value: JsonValue | None = value.get("targets")
    targets: tuple[NotificationTargetSpec, ...] = read_targets(
        reader.issues, targets_value, f"{path}/targets"
    )
    template: str | None = read_template_name(
        reader.issues, value.get("template"), f"{path}/template"
    )
    text_required: bool = True
    if v2:
        forms: int = sum((bool(to), set_name is not None, targets_value is not None))
        if forms > 1:
            reader.issues.append(ValidationIssue(path, "conflicting_fields"))
        elif forms == 0:
            reader.issues.append(ValidationIssue(path, "missing_field"))
        text_required = bool(to) and template is None
    return NotifyStep(
        *common,
        channel=channel,
        to=to,
        subject=_optional_template(reader, value.get("subject"), f"{path}/subject", text_required),
        body=_optional_template(reader, value.get("body"), f"{path}/body", text_required),
        notification_set=set_name,
        targets=targets,
        template=template,
    )


def _notify_set(reader: _Reader, value: JsonValue, path: str) -> str | None:
    set_name: str | None = (
        reader.string(value.get("notify_set"), f"{path}/notify_set", False) or None
    )
    if set_name is not None and value.get("notify_targets") is not None:
        reader.issues.append(ValidationIssue(path, "conflicting_fields"))
    return set_name


def _notify_targets(
    reader: _Reader, value: JsonValue, path: str
) -> tuple[NotificationTargetSpec, ...]:
    return read_targets(reader.issues, value.get("notify_targets"), f"{path}/notify_targets")


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


def _optional_template(
    reader: _Reader, value: JsonValue | None, path: str, required: bool
) -> Template:
    if value is None and not required:
        return ()
    return _template(reader, value, path)


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
