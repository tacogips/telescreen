"""Regression coverage for workflow engine resume gaps found during planning."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Headers
from httpx import Response as HttpxResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from telescreen.api.common import ApiContext
from telescreen.api.common.errors import install_error_handlers
from telescreen.api.routers.escalation import EscalationRouterDeps, build_escalation_router
from telescreen.api.routers.runs import RunsRouterDeps, build_runs_router
from telescreen.api.routers.workflows import WorkflowsRouterDeps, build_workflows_router
from telescreen.core.clock import ensure_utc
from telescreen.core.json import dump_json, json_object, json_str
from telescreen.core.vocabulary import (
    ApiScope,
    CollectorKind,
    EngineTaskKind,
    EngineTaskStatus,
    ErrorCode,
    IssueEventKind,
    RuleClass,
    RunStatus,
    RunTrigger,
    Severity,
    SourceFormat,
    StepType,
    TransitionKind,
    TriggerMode,
)
from telescreen.db.models import (
    EngineTaskRow,
    HumanTaskRow,
    StepRunRow,
    TransitionRow,
    WorkflowDefinitionRow,
    WorkflowRunRow,
)
from telescreen.directory.tokens import CreatedToken, TokenService
from telescreen.masking.redact import Redactor
from telescreen.ports.collectors import CollectorConfig, CustomSelector
from telescreen.ports.issues import IssueEvent
from telescreen.ports.workflow import ClaimedTask, StepSignal, StepSignalKind, TaskResult, TaskSpec
from telescreen.workflow.dsl.loader import WorkflowValidationError
from telescreen.workflow.engine.definitions import WorkflowService
from telescreen.workflow.engine.repository import WorkflowRepository
from telescreen.workflow.escalation.service import EscalationPolicyService
from telescreen.workflow.executors.flow import ConditionExecutor
from telescreen.workflow.queue.worker import TaskHandlerRegistry
from telescreen.workflow.step_api import StepContext, StepOutcome
from tests.support.samples import github_token
from tests.workflow.engine.test_engine import AuditFake, ConfigFake
from tests.workflow.engine.test_engine_extended import EngineRig, make_rig, upload_run


@dataclass
class RetryOnceExecutor(ConditionExecutor):
    """Fail the first condition visit and complete the retry."""

    calls: int = 0

    async def execute(self, ctx: StepContext) -> StepOutcome:
        """Return retryable errors until the configured attempt limit is reached."""
        self.calls += 1
        del ctx
        return StepOutcome.failed(ErrorCode.INTERNAL_ERROR, True)


@dataclass
class RawReasonExecutor:
    """Return a runtime-only sensitive send-back reason for redaction testing."""

    reason: str
    step_type: StepType = StepType.SEND_BACK

    async def execute(self, ctx: StepContext) -> StepOutcome:
        """Return the raw reason to the engine's redaction boundary."""
        del ctx
        return StepOutcome.completed(json_object((("reason", json_str(self.reason)),)))

    async def resume(self, ctx: StepContext, signal: StepSignal) -> StepOutcome:
        """Send-back steps cannot resume."""
        del ctx, signal
        return StepOutcome.failed(ErrorCode.WORKFLOW_INVALID, False)


@dataclass
class DeadRecorder:
    """Count terminal notifications emitted by the worker."""

    count: int = 0

    async def on_dead(self, task: ClaimedTask) -> None:
        """Record one terminal task notification."""
        del task
        self.count += 1


@dataclass
class RetryTaskHandler:
    """Return a retry that exceeds the durable task's configured attempt limit."""

    now: datetime
    kind: EngineTaskKind = EngineTaskKind.COLLECTOR_SCAN

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """Return a retry after the one permitted attempt."""
        del task
        return TaskResult.retry(self.now + timedelta(seconds=5), ErrorCode.INTERNAL_ERROR)


@dataclass
class CustomCollectorConfig(ConfigFake):
    """Resolve one custom collector that does not implement sweep partitioning."""

    config: CollectorConfig

    async def get_by_name(self, session: AsyncSession, name: str) -> CollectorConfig | None:
        """Return the fixture only for its configured name."""
        del session
        return self.config if name == self.config.name else None


@dataclass
class StaleRunRepository(WorkflowRepository):
    """Expose a stale loaded version to exercise the engine compare-and-swap."""

    stale_rounds: int = 1
    stale_reads: int = 0

    async def run(self, session: AsyncSession, run_id: str) -> WorkflowRunRow | None:
        """Return the loaded run with the stale version as committed state."""
        row: WorkflowRunRow | None = await super().run(session, run_id)
        if row is not None and self.stale_reads < self.stale_rounds:
            set_committed_value(row, "lock_version", 0)
            self.stale_reads += 1
        return row


@pytest.mark.asyncio
async def test_configured_retry_policy_controls_delay_and_limit() -> None:
    """Use step retry limits and backoff instead of engine-wide constants."""
    rig: EngineRig = await make_rig()
    executor: RetryOnceExecutor = RetryOnceExecutor()
    rig.engine.executors.register(executor)
    source: str = """apiVersion: telescreen.workflow/v1
kind: Workflow
name: configured-retry
start: choose
steps:
  - id: choose
    type: condition
    retry: {max_attempts: 2, backoff: PT30S}
    on_error: exhausted
    default: finish
  - id: finish
    type: end
    result: succeeded
  - id: exhausted
    type: end
    result: failed
"""
    try:
        run_id: str = await upload_run(rig, source)
        await rig.worker.run_until_idle()
        async with rig.harness.sessions() as session:
            row: EngineTaskRow | None = await session.scalar(
                select(EngineTaskRow)
                .where(
                    EngineTaskRow.kind == EngineTaskKind.ADVANCE_STEP.value,
                    EngineTaskRow.status == EngineTaskStatus.READY.value,
                )
                .order_by(EngineTaskRow.available_at.desc())
            )
            assert row is not None
            assert row.status == EngineTaskStatus.READY.value
            assert ensure_utc(row.available_at) == rig.clock.now() + timedelta(seconds=30)
        assert executor.calls == 1
        rig.clock.advance(timedelta(seconds=30))
        await rig.worker.run_until_idle()
        async with rig.harness.sessions() as session:
            run: WorkflowRunRow | None = await rig.repository.run(session, run_id)
            assert run is not None and run.status == RunStatus.FAILED.value
        assert executor.calls == 2
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_condition_type_mismatch_is_recorded_on_transition() -> None:
    """Retain the safe expression mismatch note in transition history."""
    rig: EngineRig = await make_rig()
    source: str = """apiVersion: telescreen.workflow/v1
kind: Workflow
name: condition-mismatch
start: choose
steps:
  - id: choose
    type: condition
    cases:
      - when: 'issue.times_seen > "x"'
        next: matched
    default: finish
  - id: matched
    type: end
    result: succeeded
  - id: finish
    type: end
    result: succeeded
"""
    try:
        run_id: str = await upload_run(rig, source)
        await rig.worker.run_until_idle()
        async with rig.harness.sessions() as session:
            transitions: list[TransitionRow] = list(
                await rig.repository.transitions(session, run_id)
            )
            chosen: TransitionRow = next(item for item in transitions if item.from_step_run_id)
            assert chosen.to_step_id == "finish"
            assert chosen.reason == "type_mismatch"
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_workflow_upload_rejects_sensitive_sample_before_persisting() -> None:
    """Reject monitored sample values through the direct upload service path."""
    rig: EngineRig = await make_rig()
    sample: str = github_token().value
    source: str = (
        "apiVersion: telescreen.workflow/v1\nkind: Workflow\nname: unsafe-upload\n"
        "start: finish\nsteps:\n  - id: finish\n    type: end\n    result: succeeded\n"
        "# " + sample
    )
    try:
        with pytest.raises(WorkflowValidationError) as rejected:
            async with rig.harness.sessions.begin() as session:
                await rig.service.upload(session, SourceFormat.YAML, source, "member:test")
        assert rejected.value.issues[0].code == "raw_value_detected"
        assert sample not in str(rejected.value)
        async with rig.harness.sessions() as session:
            assert (
                await session.scalar(
                    select(WorkflowDefinitionRow).where(
                        WorkflowDefinitionRow.name == "unsafe-upload"
                    )
                )
                is None
            )
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_queue_conflict_retry_refunds_attempt_but_internal_retry_does_not() -> None:
    """Keep optimistic-lock contention out of the task's terminal retry budget."""
    rig: EngineRig = await make_rig()
    try:
        now: datetime = rig.clock.now()
        async with rig.harness.sessions.begin() as session:
            await rig.queue.enqueue(
                session,
                TaskSpec(
                    EngineTaskKind.ADVANCE_STEP,
                    "conflict-step",
                    json_object(()),
                    now,
                    "conflict-retry-budget",
                    max_attempts=1,
                ),
            )
            await rig.queue.enqueue(
                session,
                TaskSpec(
                    EngineTaskKind.ADVANCE_STEP,
                    "internal-step",
                    json_object(()),
                    now,
                    "internal-retry-budget",
                    max_attempts=1,
                ),
            )
            claimed: list[ClaimedTask] = await rig.queue.claim(
                session,
                "conflict-test-worker",
                (EngineTaskKind.ADVANCE_STEP,),
                2,
                timedelta(seconds=10),
            )
            assert len(claimed) == 2
            conflict_task: ClaimedTask = next(
                task for task in claimed if task.ref_id == "conflict-step"
            )
            internal_task: ClaimedTask = next(
                task for task in claimed if task.ref_id == "internal-step"
            )
            conflict_status: EngineTaskStatus | None = await rig.queue.apply_result(
                session,
                conflict_task.id,
                TaskResult.retry(now + timedelta(seconds=1), ErrorCode.CONFLICT),
            )
            internal_status: EngineTaskStatus | None = await rig.queue.apply_result(
                session,
                internal_task.id,
                TaskResult.retry(now + timedelta(seconds=1), ErrorCode.INTERNAL_ERROR),
            )
            conflict_row: EngineTaskRow | None = await session.get(EngineTaskRow, conflict_task.id)
            internal_row: EngineTaskRow | None = await session.get(EngineTaskRow, internal_task.id)
            assert conflict_status is EngineTaskStatus.READY
            assert conflict_row is not None and conflict_row.attempts == 0
            assert ensure_utc(conflict_row.available_at) == now + timedelta(seconds=1)
            assert conflict_row.last_error_code == ErrorCode.CONFLICT.value
            assert internal_status is EngineTaskStatus.DEAD
            assert internal_row is not None and internal_row.attempts == 1
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_stale_run_version_retries_without_writing_transition() -> None:
    """Reject an advance when the loaded lock version no longer matches storage."""
    rig: EngineRig = await make_rig()
    source: str = """apiVersion: telescreen.workflow/v1
kind: Workflow
name: stale-version
start: finish
steps:
  - id: finish
    type: condition
    default: end
  - id: end
    type: end
    result: succeeded
"""
    try:
        run_id: str = await upload_run(rig, source)
        rig.engine.repository = StaleRunRepository()
        async with rig.harness.sessions.begin() as session:
            await session.execute(
                update(WorkflowRunRow).where(WorkflowRunRow.id == run_id).values(lock_version=1)
            )
        processed: int = await rig.worker.run_until_idle()
        assert processed == 1
        async with rig.harness.sessions() as session:
            step: StepRunRow = (await rig.repository.steps(session, run_id))[0]
            run: WorkflowRunRow | None = await session.get(WorkflowRunRow, run_id)
            transitions: list[TransitionRow] = list(
                (
                    await session.scalars(
                        select(TransitionRow).where(TransitionRow.run_id == run_id)
                    )
                ).all()
            )
            retry: EngineTaskRow | None = await session.scalar(
                select(EngineTaskRow).where(
                    EngineTaskRow.ref_id == step.id,
                    EngineTaskRow.kind == EngineTaskKind.ADVANCE_STEP.value,
                    EngineTaskRow.status == EngineTaskStatus.READY.value,
                )
            )
            assert run is not None and run.status == RunStatus.RUNNING.value
            assert len(transitions) == 1
            assert retry is not None
            assert ensure_utc(retry.available_at) == rig.clock.now() + timedelta(seconds=1)
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_six_stale_run_conflicts_do_not_dead_letter_advance_task() -> None:
    """Retry stale run versions beyond the task limit, then advance successfully."""
    rig: EngineRig = await make_rig()
    repository: StaleRunRepository = StaleRunRepository(stale_rounds=6)
    dead_hook: DeadRecorder = DeadRecorder()
    rig.engine.repository = repository
    rig.worker.dead_hook = dead_hook
    source: str = """apiVersion: telescreen.workflow/v1
kind: Workflow
name: repeated-stale-version
start: choose
steps:
  - id: choose
    type: condition
    default: finish
  - id: finish
    type: end
    result: succeeded
"""
    try:
        run_id: str = await upload_run(rig, source)
        async with rig.harness.sessions.begin() as session:
            await session.execute(
                update(WorkflowRunRow).where(WorkflowRunRow.id == run_id).values(lock_version=1)
            )
        for _ in range(6):
            assert await rig.worker.run_until_idle() == 1
            async with rig.harness.sessions() as session:
                step: StepRunRow = (await rig.repository.steps(session, run_id))[0]
                run: WorkflowRunRow | None = await session.get(WorkflowRunRow, run_id)
                retry: EngineTaskRow | None = await session.scalar(
                    select(EngineTaskRow).where(
                        EngineTaskRow.ref_id == step.id,
                        EngineTaskRow.kind == EngineTaskKind.ADVANCE_STEP.value,
                    )
                )
                assert run is not None and run.status == RunStatus.RUNNING.value
                assert step.status != "failed"
                assert retry is not None and retry.status == EngineTaskStatus.READY.value
                assert retry.attempts == 0
                assert retry.max_attempts == 5
            assert dead_hook.count == 0
            rig.clock.advance(timedelta(seconds=1))
        assert repository.stale_reads == 6
        await rig.worker.run_until_idle()
        async with rig.harness.sessions() as session:
            step: StepRunRow = (await rig.repository.steps(session, run_id))[0]
            run: WorkflowRunRow | None = await session.get(WorkflowRunRow, run_id)
            tasks: list[EngineTaskRow] = list((await session.scalars(select(EngineTaskRow))).all())
            assert run is not None and run.status == RunStatus.SUCCEEDED.value
            assert step.status == "succeeded"
            assert all(task.status != EngineTaskStatus.DEAD.value for task in tasks)
        assert dead_hook.count == 0
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_send_back_cancels_later_open_human_task_and_masks_reason() -> None:
    """Cancel a later approval and its SLA check while redacting the route reason."""
    rig: EngineRig = await make_rig()
    sample: str = github_token().value
    rig.engine.executors.register(RawReasonExecutor(sample))
    source: str = """apiVersion: telescreen.workflow/v1
kind: Workflow
name: send-back-open-human
start: triage
steps:
  - id: triage
    type: condition
    default: review
  - id: review
    type: human_task
    title: Review
    assignee: team:security
    outcomes: {rejected: return}
    sla: PT1H
    notify_on_assign: false
  - id: return
    type: send_back
    to: triage
    reason: safe source placeholder
    max_send_backs: 1
    on_exhausted: finish
  - id: finish
    type: end
    result: succeeded
"""
    try:
        run_id: str = await upload_run(rig, source)
        await rig.worker.run_until_idle()
        async with rig.harness.sessions() as session:
            review: StepRunRow = next(
                item
                for item in await rig.repository.steps(session, run_id)
                if item.step_id == "review"
            )
            human: HumanTaskRow | None = await session.scalar(select(HumanTaskRow))
            assert human is not None
        async with rig.harness.sessions.begin() as session:
            await rig.engine.signal(
                session, review.id, StepSignal(StepSignalKind.HUMAN_COMPLETED, "rejected")
            )
        await rig.worker.run_until_idle()
        async with rig.harness.sessions() as session:
            cancelled: HumanTaskRow | None = await session.get(HumanTaskRow, human.id)
            sla: EngineTaskRow | None = await session.scalar(
                select(EngineTaskRow).where(
                    EngineTaskRow.ref_id == human.id,
                    EngineTaskRow.kind == EngineTaskKind.SLA_CHECK.value,
                )
            )
            transitions: list[TransitionRow] = list(
                (
                    await session.scalars(
                        select(TransitionRow).where(TransitionRow.run_id == run_id)
                    )
                ).all()
            )
            send_back: TransitionRow = next(
                item for item in transitions if item.kind == TransitionKind.SEND_BACK.value
            )
            assert cancelled is not None and cancelled.status == "cancelled"
            assert sla is not None and sla.status == EngineTaskStatus.CANCELLED.value
            assert sample not in (send_back.reason or "")
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_child_loop_guard_notifies_parent_of_failure() -> None:
    """Propagate child failure when it ends at the workflow loop guard."""
    rig: EngineRig = await make_rig()
    child: str = """apiVersion: telescreen.workflow/v1
kind: Workflow
name: child-loop
defaults: {max_step_visits: 1}
start: repeat
steps:
  - id: repeat
    type: condition
    default: repeat
"""
    parent: str = """apiVersion: telescreen.workflow/v1
kind: Workflow
name: parent-loop
start: child
steps:
  - id: child
    type: sub_workflow
    workflow: child-loop
    outcomes: {succeeded: ok, failed: failed}
  - id: ok
    type: end
    result: succeeded
  - id: failed
    type: end
    result: failed
"""
    try:
        async with rig.harness.sessions.begin() as session:
            child_row: WorkflowDefinitionRow = await rig.service.upload(
                session, SourceFormat.YAML, child, "member:test"
            )
            await rig.service.activate(session, child_row.name, child_row.version)
            parent_row: WorkflowDefinitionRow = await rig.service.upload(
                session, SourceFormat.YAML, parent, "member:test"
            )
            await rig.service.activate(session, parent_row.name, parent_row.version)
            run_id: str = await rig.engine.start_run(
                session, parent_row.id, None, RunTrigger.MANUAL
            )
        await rig.worker.run_until_idle()
        async with rig.harness.sessions() as session:
            run: WorkflowRunRow | None = await rig.repository.run(session, run_id)
            assert run is not None and run.status == RunStatus.FAILED.value
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_cancelled_child_notifies_parent() -> None:
    """Resume a parent when its child run is cancelled before completion."""
    rig: EngineRig = await make_rig()
    child: str = """apiVersion: telescreen.workflow/v1
kind: Workflow
name: child-cancel
start: wait
steps:
  - id: wait
    type: wait
    duration: PT1H
    next: finish
  - id: finish
    type: end
    result: succeeded
"""
    parent: str = """apiVersion: telescreen.workflow/v1
kind: Workflow
name: parent-cancel
start: child
steps:
  - id: child
    type: sub_workflow
    workflow: child-cancel
    outcomes: {succeeded: ok, failed: failed}
  - id: ok
    type: end
    result: succeeded
  - id: failed
    type: end
    result: failed
"""
    try:
        async with rig.harness.sessions.begin() as session:
            child_row: WorkflowDefinitionRow = await rig.service.upload(
                session, SourceFormat.YAML, child, "member:test"
            )
            await rig.service.activate(session, child_row.name, child_row.version)
            parent_row: WorkflowDefinitionRow = await rig.service.upload(
                session, SourceFormat.YAML, parent, "member:test"
            )
            await rig.service.activate(session, parent_row.name, parent_row.version)
            parent_id: str = await rig.engine.start_run(
                session, parent_row.id, None, RunTrigger.MANUAL
            )
        await rig.worker.run_until_idle()
        async with rig.harness.sessions() as session:
            child_run: WorkflowRunRow | None = await session.scalar(
                select(WorkflowRunRow).where(WorkflowRunRow.parent_step_run_id.is_not(None))
            )
            assert child_run is not None
        async with rig.harness.sessions.begin() as session:
            await rig.engine.cancel_run(session, child_run.id, "member:test")
        await rig.worker.run_until_idle()
        async with rig.harness.sessions() as session:
            parent_run: WorkflowRunRow | None = await rig.repository.run(session, parent_id)
            assert parent_run is not None and parent_run.status == RunStatus.FAILED.value
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_worker_calls_dead_hook_when_retry_is_applied_as_dead() -> None:
    """Notify the dead hook when the queue converts a retry to its final dead status."""
    rig: EngineRig = await make_rig()
    dead: DeadRecorder = DeadRecorder()
    registry: TaskHandlerRegistry = TaskHandlerRegistry()
    registry.register(RetryTaskHandler(rig.clock.now()))
    rig.worker.registry = registry
    rig.worker.dead_hook = dead
    try:
        async with rig.harness.sessions.begin() as session:
            await rig.queue.enqueue(
                session,
                TaskSpec(
                    EngineTaskKind.COLLECTOR_SCAN,
                    "safe-ref",
                    json_object(()),
                    rig.clock.now(),
                    "dead-hook-retry",
                    max_attempts=1,
                ),
            )
        await rig.worker.run_until_idle()
        assert dead.count == 1
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_map_upload_rejects_collector_without_sweep_support() -> None:
    """Reject a custom collector as a map partition source without sweep support."""
    rig: EngineRig = await make_rig()
    config: CollectorConfig = CollectorConfig(
        "collector-id",
        "custom-source",
        CollectorKind.CUSTOM,
        TriggerMode.EVENT,
        None,
        None,
        1,
        CustomSelector(json_object(())),
        None,
        True,
    )
    service: WorkflowService = WorkflowService(
        Redactor.default(),
        CustomCollectorConfig(config),
        AuditFake([]),
        rig.clock,
        rig.repository,
    )
    source: str = """apiVersion: telescreen.workflow/v1
kind: Workflow
name: unsupported-map
start: sweep
steps:
  - id: sweep
    type: map
    source:
      partition: {via: collector, collector: custom-source}
    map: {via: collector}
    outcomes: {succeeded: finish, partial: finish, failed: finish}
  - id: finish
    type: end
    result: succeeded
"""
    try:
        with pytest.raises(WorkflowValidationError) as unsupported:
            async with rig.harness.sessions.begin() as session:
                await service.upload(session, SourceFormat.YAML, source, "member:test")
        assert unsupported.value.issues[0].code == "sweep_unsupported"
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_workflow_upload_audit_contains_only_definition_metadata() -> None:
    """Keep uploaded workflow bodies out of audit details."""
    rig: EngineRig = await make_rig()
    audit: AuditFake = AuditFake([])
    service: WorkflowService = WorkflowService(
        Redactor.default(), ConfigFake(), audit, rig.clock, rig.repository
    )
    source: str = """apiVersion: telescreen.workflow/v1
kind: Workflow
name: audited-workflow
start: finish
steps:
  - id: finish
    type: end
    result: succeeded
"""
    try:
        async with rig.harness.sessions.begin() as session:
            row: WorkflowDefinitionRow = await service.upload(
                session, SourceFormat.YAML, source, "member:test"
            )
        details: str = dump_json(audit.entries[0].details)
        assert row.id in details
        assert '"version":1' in details
        assert "compiled" not in details and "source_text" not in details
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_trigger_deduplicates_active_run_across_workflow_versions() -> None:
    """An active v1 run prevents a trigger from starting the active v2 definition."""
    rig: EngineRig = await make_rig()
    source: str = """apiVersion: telescreen.workflow/v1
kind: Workflow
name: versioned-trigger
start: wait
trigger:
  "on": [issue_created]
steps:
  - id: wait
    type: wait
    duration: PT1H
    next: finish
  - id: finish
    type: end
    result: succeeded
"""
    try:
        async with rig.harness.sessions.begin() as session:
            first: WorkflowDefinitionRow = await rig.service.upload(
                session, SourceFormat.YAML, source, "member:test"
            )
            await rig.service.activate(session, first.name, first.version)
            await rig.engine.start_run(session, first.id, "issue-v", RunTrigger.MANUAL)
        await rig.worker.run_until_idle()
        async with rig.harness.sessions.begin() as session:
            second: WorkflowDefinitionRow = await rig.service.upload(
                session, SourceFormat.YAML, source, "member:test"
            )
            await rig.service.activate(session, second.name, second.version)
            from telescreen.workflow.triggers.service import WorkflowTriggerService

            await WorkflowTriggerService(rig.engine, rig.repository).on_issue_event(
                session,
                IssueEvent(
                    IssueEventKind.ISSUE_CREATED,
                    "event-v",
                    "issue-v",
                    "github",
                    RuleClass.CREDENTIAL,
                    Severity.HIGH,
                    rig.clock.now(),
                ),
            )
        async with rig.harness.sessions() as session:
            runs: list[WorkflowRunRow] = list((await session.scalars(select(WorkflowRunRow))).all())
            assert len(runs) == 1
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_workflow_run_and_escalation_routers_cover_write_read_and_masking() -> None:
    """Exercise the assigned workflow, run, and escalation APIs over ASGI."""
    rig: EngineRig = await make_rig()
    redactor: Redactor = Redactor.default()
    audit: AuditFake = AuditFake([])
    policy_service: EscalationPolicyService = EscalationPolicyService(audit, rig.clock, redactor)
    ctx: ApiContext = ApiContext(rig.harness.sessions, rig.clock, audit)
    app: FastAPI = FastAPI()
    install_error_handlers(app)
    app.include_router(build_workflows_router(WorkflowsRouterDeps(ctx, rig.service, rig.engine)))
    app.include_router(build_runs_router(RunsRouterDeps(ctx, rig.engine, rig.repository)))
    app.include_router(build_escalation_router(EscalationRouterDeps(ctx, policy_service)))
    try:
        async with rig.harness.sessions.begin() as session:
            admin_token: CreatedToken = await TokenService(audit, rig.clock).create(
                session,
                "workflow-router-admin",
                frozenset((ApiScope.WRITE, ApiScope.ADMIN)),
                None,
            )
            read_token: CreatedToken = await TokenService(audit, rig.clock).create(
                session, "workflow-router-reader", frozenset((ApiScope.READ,)), None
            )
        headers: Headers = Headers({"Authorization": f"Bearer {admin_token.token}"})
        read_headers: Headers = Headers({"Authorization": f"Bearer {read_token.token}"})
        source: str = """apiVersion: telescreen.workflow/v1
kind: Workflow
name: router-workflow
start: wait
steps:
  - id: wait
    type: wait
    duration: PT1H
    next: finish
  - id: finish
    type: end
    result: succeeded
"""
        sample: str = github_token().value
        raw_source: str = source + "\n# " + sample
        policy_text: str = """name: router-policy
tiers:
  - after: PT1H
    assign_to: team:security
    notify: [team:security]
    channels: [email]
"""
        transport: ASGITransport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            validated: HttpxResponse = await client.post(
                "/api/workflows/validate",
                headers=headers,
                json={"source_format": "yaml", "source_text": source},
            )
            assert validated.status_code == 200 and '"valid":true' in validated.text
            rejected: HttpxResponse = await client.post(
                "/api/workflows",
                headers=headers,
                json={"source_format": "yaml", "source_text": raw_source},
            )
            assert rejected.status_code == 422
            assert sample not in rejected.text
            uploaded: HttpxResponse = await client.post(
                "/api/workflows",
                headers=headers,
                json={"source_format": "yaml", "source_text": source},
            )
            assert uploaded.status_code == 201
            version: int = uploaded.json()["version"]
            activated: HttpxResponse = await client.post(
                "/api/workflows/router-workflow/activate",
                headers=headers,
                json={"version": version},
            )
            assert activated.status_code == 200
            started: HttpxResponse = await client.post(
                "/api/workflows/router-workflow/runs", headers=headers, json={}
            )
            assert started.status_code == 201
            run_id: str = started.json()["run_id"]
            await rig.worker.run_until_idle()
            listed: HttpxResponse = await client.get(
                "/api/runs?workflow=router-workflow", headers=headers
            )
            fetched: HttpxResponse = await client.get(f"/api/runs/{run_id}", headers=headers)
            assert listed.status_code == 200 and run_id in listed.text
            assert fetched.status_code == 200 and run_id in fetched.text
            cancelled: HttpxResponse = await client.post(
                f"/api/runs/{run_id}/cancel", headers=headers
            )
            assert cancelled.status_code == 200 and '"status":"cancelled"' in cancelled.text
            created_policy: HttpxResponse = await client.post(
                "/api/escalation-policies",
                headers=headers,
                json={"tiers_json": policy_text, "source_format": "yaml"},
            )
            assert created_policy.status_code == 201
            updated_policy: HttpxResponse = await client.put(
                "/api/escalation-policies/router-policy",
                headers=headers,
                json={"tiers_json": policy_text, "source_format": "yaml"},
            )
            policies: HttpxResponse = await client.get(
                "/api/escalation-policies", headers=read_headers
            )
            assert updated_policy.status_code == 200
            assert policies.status_code == 200 and "router-policy" in policies.text
            unsafe_policy: HttpxResponse = await client.post(
                "/api/escalation-policies",
                headers=headers,
                json={
                    "tiers_json": policy_text + "# " + sample,
                    "source_format": "yaml",
                },
            )
            assert unsafe_policy.status_code == 422
            assert sample not in unsafe_policy.text
    finally:
        await rig.close()
