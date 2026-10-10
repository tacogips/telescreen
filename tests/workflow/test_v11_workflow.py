"""Regression coverage for v1.1 workflow DSL and executor behavior."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import FakeClock
from telescreen.core.json import JsonValue, dump_json, json_object
from telescreen.core.vocabulary import (
    DetectorKind,
    DlpVerdict,
    EngineTaskKind,
    ErrorCode,
    HumanTaskKind,
    IssueHistoryKind,
    IssueStatus,
    NotificationChannel,
    RuleClass,
    Severity,
    SourceFormat,
    Visibility,
)
from telescreen.db.models import (
    EngineTaskRow,
    EscalationPolicyRow,
    HumanTaskRow,
    WorkflowRunRow,
)
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry
from telescreen.ports.collectors import RescanKind, RescanResult
from telescreen.ports.directory import AssigneeIds, ResolvedRecipients
from telescreen.ports.findings import ExternalScan, RecordOutcome, ResourceScan
from telescreen.ports.issues import IssueHistoryEntry, IssueSnapshot, OpenResourceRef
from telescreen.ports.notifications import NotificationReceipt, NotificationRequest
from telescreen.ports.workflow import ClaimedTask, TaskResult
from telescreen.workflow.dsl.context import RunContext, RunInfo
from telescreen.workflow.dsl.escalation import (
    EscalationPolicySpec,
    EscalationTier,
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
    DetectStep,
    HumanTaskStep,
    NamedRoute,
    Outcomes3,
)
from telescreen.workflow.dsl.templates import parse_template
from telescreen.workflow.engine.repository import WorkflowRepository
from telescreen.workflow.escalation.sla import SlaCheckHandler
from telescreen.workflow.executors.detect import DetectExecutor
from telescreen.workflow.executors.human import HumanTaskExecutor
from telescreen.workflow.queue.sql_queue import SqlTaskQueue
from telescreen.workflow.step_api import StepContext, StepOutcome, StepOutcomeKind, WaitKind
from tests.support.db import SqliteHarness, make_sqlite_harness

NOW: datetime = datetime(2026, 10, 10, 0, 0, tzinfo=UTC)


@dataclass
class IssueReaderFake:
    """Return one configured masked issue snapshot."""

    snapshot: IssueSnapshot | None

    async def get(self, session: AsyncSession, issue_id: str) -> IssueSnapshot | None:
        del session, issue_id
        return self.snapshot

    async def open_resource_refs(
        self, session: AsyncSession, collector_config_id: str
    ) -> tuple[OpenResourceRef, ...]:
        del session, collector_config_id
        return ()


@dataclass
class ResolverFake:
    """Resolve one reference to configured safe notification addresses."""

    result: ResolvedRecipients

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients:
        del session, ref
        return self.result

    async def is_member_of(self, session: AsyncSession, member_id: str, ref: str) -> bool:
        del session, member_id, ref
        return True

    async def assignee_of(self, session: AsyncSession, ref: str) -> AssigneeIds:
        del session, ref
        return AssigneeIds("member-id", None)


@dataclass
class NotificationFake:
    """Collect queued notifications without external delivery."""

    requests: list[NotificationRequest] = field(default_factory=list)

    async def send(
        self, session: AsyncSession, request: NotificationRequest
    ) -> NotificationReceipt:
        del session
        self.requests.append(request)
        return NotificationReceipt(("notification-1",), DlpVerdict.PASS, True)


@dataclass
class HistoryFake:
    """Collect issue history writes."""

    entries: list[IssueHistoryEntry] = field(default_factory=list)

    async def record_scan(self, session: AsyncSession, scan: ResourceScan) -> RecordOutcome:
        del session, scan
        raise NotImplementedError

    async def record_external(self, session: AsyncSession, scan: ExternalScan) -> RecordOutcome:
        del session, scan
        raise NotImplementedError

    async def add_history(self, session: AsyncSession, entry: IssueHistoryEntry) -> None:
        del session
        self.entries.append(entry)

    async def resolve_unchecked(
        self, session: AsyncSession, collector_config_id: str, checked_before: datetime
    ) -> int:
        del session, collector_config_id, checked_before
        raise NotImplementedError


@dataclass
class AuditFake:
    """Collect audit entries."""

    entries: list[AuditEntry] = field(default_factory=list)

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        del session
        self.entries.append(entry)
        return len(self.entries)


@dataclass
class RescannerFake:
    """Return one configured rescan result."""

    result: RescanResult

    async def rescan(
        self, issue_id: str, detectors: tuple[DetectorKind, ...] | None = None
    ) -> RescanResult:
        del issue_id, detectors
        return self.result


def _snapshot(risk_score: int = 40, assignee_ref: str | None = "member:alice") -> IssueSnapshot:
    """Build a masked issue snapshot for executor tests."""
    return IssueSnapshot(
        "issue-1",
        8,
        "TSC-8",
        "private-file.txt",
        IssueStatus.UNRESOLVED,
        "github",
        RuleClass.CREDENTIAL,
        Severity.HIGH,
        "org/repo/private-file.txt",
        1,
        NOW,
        NOW,
        "collector-1",
        None,
        assignee_ref,
        risk_score,
        Visibility.PUBLIC,
    )


def _step_context(session: AsyncSession, step: HumanTaskStep, issue_id: str | None) -> StepContext:
    """Create a step context containing no raw issue fields."""
    return StepContext(
        session,
        "run-1",
        "step-run-1",
        step,
        1,
        1,
        RunContext(None, RunInfo("run-1", "workflow", NOW.isoformat())),
        issue_id,
        NOW,
    )


def _human_step(assignee: str = "issue:assignee") -> HumanTaskStep:
    """Create an owner-review human task with its fixed outcome names."""
    return HumanTaskStep(
        "review",
        title=parse_template("private-file.txt"),
        assignee=assignee,
        outcomes=(
            NamedRoute("remove", "done"),
            NamedRoute("restrict", "done"),
            NamedRoute("exception", "done"),
            NamedRoute("not_mine", "done"),
        ),
        kind=HumanTaskKind.OWNER_REVIEW,
    )


def _issue_snapshot(risk_score: int) -> IssueSnapshot:
    return _snapshot(risk_score)


@pytest.mark.parametrize("workflow_path", sorted(Path("examples/workflows").glob("*.yaml")))
def test_example_workflows_keep_compiled_json_round_trip(workflow_path: Path) -> None:
    """Existing YAML examples retain their compiled model through the moved serializer."""
    workflow: CompiledWorkflow = load_workflow(
        workflow_path.read_text(encoding="utf-8"), SourceFormat.YAML
    )
    encoded: JsonValue = compiled_to_json(workflow)
    assert compiled_from_json(encoded) == workflow
    for step in workflow.steps:
        if isinstance(step, HumanTaskStep):
            assert encoded.get("steps") is not None


def test_owner_review_has_fixed_outcomes_and_compiles_kind() -> None:
    """Owner-review tasks round-trip their kind while approval remains the default."""
    source: str = """apiVersion: telescreen.workflow/v1
kind: Workflow
name: owner-review
start: review
steps:
  - id: review
    type: human_task
    kind: owner_review
    title: Review issue
    assignee: issue:assignee
    outcomes: {exception: done, not_mine: done, remove: done, restrict: done}
  - id: done
    type: end
    result: succeeded
"""
    workflow: CompiledWorkflow = load_workflow(source, SourceFormat.YAML)
    compiled: JsonValue = compiled_to_json(workflow)
    steps: JsonValue | None = compiled.get("steps")
    assert steps is not None
    kind_value: JsonValue | None = steps.items[0].get("kind")
    review_kind: str | None = kind_value.as_str() if kind_value is not None else None
    assert review_kind == "owner_review"
    assert compiled_from_json(compiled) == workflow

    approval_source: str = source.replace("    kind: owner_review\n", "").replace(
        "issue:assignee", "member:alice"
    )
    approval_workflow: CompiledWorkflow = load_workflow(approval_source, SourceFormat.YAML)
    approval_json: JsonValue = compiled_to_json(approval_workflow)
    approval_steps: JsonValue | None = approval_json.get("steps")
    assert approval_steps is not None
    assert approval_steps.items[0].get("kind") is None


def test_owner_review_validation_rejects_bad_kind_and_outcomes() -> None:
    """Owner-review configuration rejects unsupported kinds and outcome sets."""
    source: str = """apiVersion: telescreen.workflow/v1
kind: Workflow
name: owner-review-invalid
start: review
steps:
  - id: review
    type: human_task
    kind: owner_review
    title: Review
    assignee: issue:assignee
    outcomes: {remove: done, restrict: done, exception: done}
  - id: done
    type: end
    result: succeeded
"""
    with pytest.raises(WorkflowValidationError) as outcomes_error:
        load_workflow(source, SourceFormat.YAML)
    assert any(item.code == "owner_review_outcomes" for item in outcomes_error.value.issues)
    with pytest.raises(WorkflowValidationError) as kind_error:
        load_workflow(source.replace("kind: owner_review", "kind: foo"), SourceFormat.YAML)
    assert any(item.code == "invalid_value" for item in kind_error.value.issues)


def test_critical_trigger_and_issue_assignee_reference_parse() -> None:
    """The new severity and deferred owner reference pass static validation."""
    source: str = """apiVersion: telescreen.workflow/v1
kind: Workflow
name: critical-trigger
start: done
trigger:
  "on": [issue_created]
  match: {min_severity: critical}
steps:
  - id: done
    type: end
    result: succeeded
"""
    workflow: CompiledWorkflow = load_workflow(source, SourceFormat.YAML)
    assert workflow.trigger is not None
    assert workflow.trigger.match.min_severity is Severity.CRITICAL


def test_escalation_min_risk_validates_and_round_trips() -> None:
    """Escalation risk thresholds are bounded and persist in compiled JSON."""
    source: str = """name: security
tiers:
  - after: PT5M
    assign_to: team:on-call
    notify: [team:on-call]
    channels: [email]
    min_risk: 50
"""
    policy: EscalationPolicySpec = load_escalation_policy(source, SourceFormat.YAML)
    assert policy.tiers[0].min_risk == 50
    assert escalation_from_json(escalation_to_json(policy)) == policy
    with pytest.raises(WorkflowValidationError) as error:
        load_escalation_policy(source.replace("min_risk: 50", "min_risk: 150"), SourceFormat.YAML)
    assert any(item.code == "invalid_value" for item in error.value.issues)


@pytest.mark.asyncio
async def test_detect_inconclusive_routes_to_scan_incomplete() -> None:
    """Incomplete rescans take the error route with a stable scan code."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        async with harness.sessions() as session:
            step: DetectStep = DetectStep(
                "scan", outcomes=Outcomes3(error="incomplete", clean="clean", found="found")
            )
            ctx: StepContext = StepContext(
                session,
                "run-1",
                "step-run-1",
                step,
                1,
                1,
                RunContext(None, RunInfo("run-1", "workflow", NOW.isoformat())),
                "issue-1",
                NOW,
            )
            executor: DetectExecutor = DetectExecutor(
                RescannerFake(RescanResult(RescanKind.INCONCLUSIVE))
            )
            outcome: StepOutcome = await executor.execute(ctx)
            assert outcome.route == "incomplete"
            assert outcome.output.get("error_code") is not None
            error_value: JsonValue | None = outcome.output.get("error_code")
            assert error_value is not None
            assert error_value.as_str() == "scan_incomplete"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_owner_review_resolves_issue_assignee_and_sends_one_safe_channel() -> None:
    """Owner review stores the resolved assignee and omits title and locator from delivery."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        async with harness.sessions() as session:
            notifications: NotificationFake = NotificationFake()
            executor: HumanTaskExecutor = HumanTaskExecutor(
                notifications,
                SqlTaskQueue(FakeClock(NOW)),
                ResolverFake(ResolvedRecipients(("alice@example.invalid",), (), ("U-A",), ())),
                FakeClock(NOW),
                Redactor.default(),
                issues=IssueReaderFake(_snapshot()),
                public_base_url="https://telescreen.example",
            )
            outcome: StepOutcome = await executor.execute(
                _step_context(session, _human_step(), "issue-1")
            )
            assert outcome.kind is StepOutcomeKind.WAITING
            assert outcome.wait_kind is WaitKind.HUMAN
            task: HumanTaskRow | None = await session.scalar(select(HumanTaskRow))
            assert task is not None
            assert task.kind == HumanTaskKind.OWNER_REVIEW.value
            assert task.assignee_ref == "member:alice"
            assert len(notifications.requests) == 1
            request: NotificationRequest = notifications.requests[0]
            assert request.channel is NotificationChannel.SLACK
            assert request.subject == "Telescreen: review TELE-8"
            assert "TELE-8" in request.body
            assert "credential high public" in request.body
            assert "risk 40" in request.body
            assert "https://telescreen.example/my-tasks" in request.body
            assert "private-file.txt" not in request.body
            assert "org/repo/private-file.txt" not in request.body
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_owner_review_without_resolved_assignee_fails_safely() -> None:
    """An owner review with no issue assignee fails before persisting a task."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        async with harness.sessions() as session:
            executor: HumanTaskExecutor = HumanTaskExecutor(
                NotificationFake(),
                SqlTaskQueue(FakeClock(NOW)),
                ResolverFake(ResolvedRecipients((), (), (), ())),
                FakeClock(NOW),
                Redactor.default(),
                issues=IssueReaderFake(_snapshot(assignee_ref=None)),
            )
            outcome: StepOutcome = await executor.execute(
                _step_context(session, _human_step(), "issue-1")
            )
            assert outcome.kind is StepOutcomeKind.FAILED
            assert outcome.error_code is ErrorCode.RECIPIENT_UNKNOWN
            assert await session.scalar(select(HumanTaskRow)) is None
    finally:
        await harness.close()


@pytest.mark.parametrize(("risk_score", "escalates"), ((40, False), (90, True)))
@pytest.mark.asyncio
async def test_sla_min_risk_stops_below_threshold(risk_score: int, escalates: bool) -> None:
    """SLA threshold blocks low-risk reassignment and permits high-risk escalation."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(NOW)
    history: HistoryFake = HistoryFake()
    audit: AuditFake = AuditFake()
    notifications: NotificationFake = NotificationFake()
    task_id: str = "human-task-1"
    run_id: str = "run-1"
    policy_id: str = "policy-1"
    try:
        async with harness.sessions.begin() as session:
            session.add(
                WorkflowRunRow(
                    id=run_id,
                    definition_id="definition-1",
                    issue_id="issue-1",
                    status="waiting",
                    trigger="manual",
                    context_json="{}",
                    started_at=NOW,
                )
            )
            session.add(
                EscalationPolicyRow(
                    id=policy_id,
                    name="security",
                    tiers_json=dump_json(
                        escalation_to_json(
                            EscalationPolicySpec(
                                "security",
                                (
                                    EscalationTier(
                                        timedelta(minutes=5),
                                        "team:on-call",
                                        (),
                                        (),
                                        80,
                                    ),
                                ),
                            )
                        )
                    ),
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            session.add(
                HumanTaskRow(
                    id=task_id,
                    run_id=run_id,
                    step_run_id="step-run-1",
                    kind="approval",
                    title="Review issue",
                    assignee_ref="team:first-line",
                    escalation_policy_id=policy_id,
                    tier_index=0,
                    due_at=NOW,
                    status="open",
                )
            )
        handler: SlaCheckHandler = SlaCheckHandler(
            harness.sessions,
            WorkflowRepository(),
            SqlTaskQueue(clock),
            notifications,
            ResolverFake(ResolvedRecipients((), (), (), ())),
            history,
            audit,
            clock,
            reader=IssueReaderFake(_issue_snapshot(risk_score)),
        )
        result: TaskResult = await handler.handle(
            ClaimedTask(
                "sla-task",
                EngineTaskKind.SLA_CHECK,
                task_id,
                json_object(()),
                1,
                5,
                "sla-key",
            )
        )
        assert result.kind.value == "done"
        async with harness.sessions() as session:
            task: HumanTaskRow | None = await session.get(HumanTaskRow, task_id)
            assert task is not None
            if escalates:
                assert task.assignee_ref == "team:on-call"
                assert task.tier_index == 1
                assert history.entries[0].kind is IssueHistoryKind.SLA_BREACHED
                assert history.entries[0].note == "Human task SLA breached"
                queued: list[EngineTaskRow] = list(
                    (await session.scalars(select(EngineTaskRow))).all()
                )
                assert len(queued) == 1
            else:
                assert task.assignee_ref == "team:first-line"
                assert task.tier_index == 0
                assert history.entries[0].kind is IssueHistoryKind.SLA_BREACHED
                assert history.entries[0].note == "below risk threshold"
                assert audit.entries[0].action == "sla.breached"
                assert await session.scalar(select(EngineTaskRow)) is None
    finally:
        await harness.close()
