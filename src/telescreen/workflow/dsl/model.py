"""Typed immutable model for workflow definitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import ClassVar

from telescreen.core.json import JsonValue
from telescreen.core.vocabulary import (
    DetectorKind,
    IssueEventKind,
    NotificationChannel,
    RuleClass,
    RunStatus,
    Severity,
    StepType,
)


class AgentProvider(StrEnum):
    """Supported workflow agent provider categories."""

    MANAGED = "managed"
    LOCAL = "local"


class OutputType(StrEnum):
    """Types accepted for structured agent output fields."""

    STRING = "string"
    BOOLEAN = "boolean"
    NUMBER = "number"
    ENUM = "enum"


class JoinMode(StrEnum):
    """Parallel-branch join policy."""

    ALL = "all"
    ANY = "any"


class MapVia(StrEnum):
    """Adapter category used to partition and process map shards."""

    COLLECTOR = "collector"
    POLICE = "police"


@dataclass(frozen=True)
class Expression:
    """Parsed expression node with a closed recursive tree."""

    kind: str
    value: JsonValue | None = None
    path: tuple[str, ...] = ()
    children: tuple[Expression, ...] = ()


@dataclass(frozen=True)
class TemplatePart:
    """Literal text or a context path in a parsed template."""

    literal: str | None = None
    path: tuple[str, ...] = ()


type Template = tuple[TemplatePart, ...]


@dataclass(frozen=True)
class RetrySpec:
    """Retry limit and base delay."""

    max_attempts: int
    backoff: timedelta


@dataclass(frozen=True)
class TriggerMatch:
    """Optional filters for issue-triggered workflows."""

    sources: tuple[str, ...] = ()
    rule_classes: tuple[RuleClass, ...] = ()
    min_severity: Severity | None = None


@dataclass(frozen=True)
class TriggerSpec:
    """Issue events that start a workflow."""

    on: tuple[IssueEventKind, ...]
    match: TriggerMatch


@dataclass(frozen=True)
class Defaults:
    """Workflow-wide execution defaults."""

    max_step_visits: int = 20
    retry: RetrySpec | None = None


@dataclass(frozen=True)
class Outcomes3:
    """Named routes for found/clean/error or passed/failed/error outcomes."""

    found: str | None = None
    clean: str | None = None
    error: str | None = None
    passed: str | None = None
    failed: str | None = None
    succeeded: str | None = None
    partial: str | None = None


@dataclass(frozen=True)
class OutputField:
    """One declared structured output field."""

    name: str
    type: OutputType
    values: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConditionCase:
    """Conditional route with a parsed expression."""

    when: Expression
    next: str


@dataclass(frozen=True)
class NamedRoute:
    """Named outcome route."""

    name: str
    to: str


@dataclass(frozen=True)
class NamedTemplate:
    """Named string template."""

    name: str
    template: Template


@dataclass(frozen=True)
class DetectStep:
    """Rescan the issue's resource."""

    id: str
    name: str | None = None
    next: str | None = None
    retry: RetrySpec | None = None
    on_error: str | None = None
    detectors: tuple[DetectorKind, ...] | None = None
    outcomes: Outcomes3 = Outcomes3()
    step_type: ClassVar[StepType] = StepType.DETECT


@dataclass(frozen=True)
class AiAgentStep:
    """Call a configured agent and validate its structured response."""

    id: str
    name: str | None = None
    next: str | None = None
    retry: RetrySpec | None = None
    on_error: str | None = None
    provider: AgentProvider = AgentProvider.MANAGED
    model: str | None = None
    prompt: Template = ()
    output: tuple[OutputField, ...] = ()
    step_type: ClassVar[StepType] = StepType.AI_AGENT


@dataclass(frozen=True)
class ConditionStep:
    """Branch to the first matching case or default route."""

    id: str
    name: str | None = None
    next: str | None = None
    retry: RetrySpec | None = None
    on_error: str | None = None
    cases: tuple[ConditionCase, ...] = ()
    default: str = ""
    step_type: ClassVar[StepType] = StepType.CONDITION


@dataclass(frozen=True)
class HumanTaskStep:
    """Wait for a human-selected outcome."""

    id: str
    name: str | None = None
    next: str | None = None
    retry: RetrySpec | None = None
    on_error: str | None = None
    title: Template = ()
    assignee: str = ""
    outcomes: tuple[NamedRoute, ...] = ()
    sla: timedelta | None = None
    escalation_policy: str | None = None
    notify_on_assign: bool = True
    step_type: ClassVar[StepType] = StepType.HUMAN_TASK


@dataclass(frozen=True)
class NotifyStep:
    """Queue a DLP-checked notification."""

    id: str
    name: str | None = None
    next: str | None = None
    retry: RetrySpec | None = None
    on_error: str | None = None
    channel: NotificationChannel = NotificationChannel.EMAIL
    to: tuple[str, ...] = ()
    subject: Template = ()
    body: Template = ()
    step_type: ClassVar[StepType] = StepType.NOTIFY


@dataclass(frozen=True)
class EvidenceUploadStep:
    """Wait for an accepted evidence upload."""

    id: str
    name: str | None = None
    next: str | None = None
    retry: RetrySpec | None = None
    on_error: str | None = None
    title: Template = ()
    assignee: str = ""
    min_files: int = 1
    sla: timedelta | None = None
    escalation_policy: str | None = None
    step_type: ClassVar[StepType] = StepType.EVIDENCE_UPLOAD


@dataclass(frozen=True)
class PoliceVerifyStep:
    """Dispatch a verification task to a Police worker."""

    id: str
    name: str | None = None
    next: str | None = None
    retry: RetrySpec | None = None
    on_error: str | None = None
    task_type: str = "verify_remediation"
    labels: tuple[str, ...] = ()
    input: tuple[NamedTemplate, ...] = ()
    timeout: timedelta = timedelta(hours=1)
    outcomes: Outcomes3 = Outcomes3()
    step_type: ClassVar[StepType] = StepType.POLICE_VERIFY


@dataclass(frozen=True)
class WaitStep:
    """Wait for a timer before continuing."""

    id: str
    name: str | None = None
    next: str | None = None
    retry: RetrySpec | None = None
    on_error: str | None = None
    duration: timedelta = timedelta(0)
    step_type: ClassVar[StepType] = StepType.WAIT


@dataclass(frozen=True)
class SendBackStep:
    """Return execution to an earlier step, with a bounded loop count."""

    id: str
    name: str | None = None
    next: str | None = None
    retry: RetrySpec | None = None
    on_error: str | None = None
    to: str = ""
    reason: Template = ()
    max_send_backs: int = 3
    on_exhausted: str | None = None
    step_type: ClassVar[StepType] = StepType.SEND_BACK


@dataclass(frozen=True)
class ParallelStep:
    """Start concurrent branches and wait at a join."""

    id: str
    name: str | None = None
    next: str | None = None
    retry: RetrySpec | None = None
    on_error: str | None = None
    branches: tuple[str, ...] = ()
    join: str = ""
    step_type: ClassVar[StepType] = StepType.PARALLEL


@dataclass(frozen=True)
class JoinStep:
    """Join parallel branches."""

    id: str
    name: str | None = None
    next: str | None = None
    retry: RetrySpec | None = None
    on_error: str | None = None
    mode: JoinMode = JoinMode.ALL
    step_type: ClassVar[StepType] = StepType.JOIN


@dataclass(frozen=True)
class SubWorkflowStep:
    """Start another workflow definition."""

    id: str
    name: str | None = None
    next: str | None = None
    retry: RetrySpec | None = None
    on_error: str | None = None
    workflow: str = ""
    version: int | None = None
    outcomes: Outcomes3 = Outcomes3()
    step_type: ClassVar[StepType] = StepType.SUB_WORKFLOW


@dataclass(frozen=True)
class MapStep:
    """Partition source resources and process shards with bounded concurrency."""

    id: str
    name: str | None = None
    next: str | None = None
    retry: RetrySpec | None = None
    on_error: str | None = None
    partition_via: MapVia = MapVia.COLLECTOR
    collector: str = ""
    partition_task_type: str | None = None
    partition_labels: tuple[str, ...] = ()
    map_via: MapVia = MapVia.COLLECTOR
    map_task_type: str | None = None
    map_labels: tuple[str, ...] = ()
    max_concurrency: int = 16
    shard_timeout: timedelta = timedelta(minutes=5)
    shard_max_attempts: int = 3
    max_failed_ratio: float = 0.0
    max_failed_items: int = 0
    timeout: timedelta = timedelta(hours=1)
    outcomes: Outcomes3 = Outcomes3()
    step_type: ClassVar[StepType] = StepType.MAP


@dataclass(frozen=True)
class EndStep:
    """Terminate a workflow run."""

    id: str
    name: str | None = None
    next: str | None = None
    retry: RetrySpec | None = None
    on_error: str | None = None
    result: RunStatus = RunStatus.SUCCEEDED
    step_type: ClassVar[StepType] = StepType.END


type StepSpec = (
    DetectStep
    | AiAgentStep
    | ConditionStep
    | HumanTaskStep
    | NotifyStep
    | EvidenceUploadStep
    | PoliceVerifyStep
    | WaitStep
    | SendBackStep
    | ParallelStep
    | JoinStep
    | SubWorkflowStep
    | MapStep
    | EndStep
)


@dataclass(frozen=True)
class CompiledWorkflow:
    """Validated workflow definition consumed by the engine."""

    name: str
    description: str | None
    trigger: TriggerSpec | None
    defaults: Defaults
    start: str
    steps: tuple[StepSpec, ...]

    def step(self, step_id: str) -> StepSpec:
        """Return a step by id or raise KeyError."""
        for step in self.steps:
            if step.id == step_id:
                return step
        raise KeyError(step_id)
