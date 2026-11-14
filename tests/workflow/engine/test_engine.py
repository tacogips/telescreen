"""SQLite behavior tests for workflow queue durability and run progression."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import FakeClock
from telescreen.core.json import json_object, json_str
from telescreen.core.vocabulary import (
    EngineTaskKind,
    EngineTaskStatus,
    ErrorCode,
    RunStatus,
    RunTrigger,
    SourceFormat,
    StepType,
)
from telescreen.db.models import (
    EngineTaskRow,
    StepRunRow,
    WorkflowDefinitionRow,
    WorkflowRunRow,
)
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry
from telescreen.ports.collectors import CollectorConfig
from telescreen.ports.findings import ExternalScan, RecordOutcome, ResourceScan
from telescreen.ports.issues import IssueHistoryEntry, IssueSnapshot, OpenResourceRef
from telescreen.ports.workflow import ClaimedTask, StepSignal, TaskSpec
from telescreen.workflow.dsl.validate import ValidationIssue
from telescreen.workflow.engine.definitions import WorkflowService
from telescreen.workflow.engine.engine import AdvanceStepHandler, RunEngine, TimerFiredHandler
from telescreen.workflow.engine.repository import WorkflowRepository
from telescreen.workflow.executors import ExecutorRegistry
from telescreen.workflow.executors.flow import (
    ConditionExecutor,
    EndExecutor,
    JoinExecutor,
    ParallelExecutor,
    WaitExecutor,
)
from telescreen.workflow.queue.sql_queue import SqlTaskQueue
from telescreen.workflow.queue.worker import EngineWorker, TaskHandlerRegistry
from telescreen.workflow.scheduler.service import Scheduler
from telescreen.workflow.step_api import StepContext, StepOutcome
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import github_token


@dataclass
class RetryOutputExecutor:
    """Fail once, then return a sensitive string for engine redaction."""

    secret: str
    attempts: int = 0
    step_type: StepType = StepType.CONDITION

    async def execute(self, ctx: StepContext) -> StepOutcome:
        del ctx
        self.attempts += 1
        if self.attempts == 1:
            return StepOutcome.failed(ErrorCode.INTERNAL_ERROR, True)
        return StepOutcome.completed(json_object((("sample", json_str(self.secret)),)), "finish")

    async def resume(self, ctx: StepContext, signal: StepSignal) -> StepOutcome:
        del ctx, signal
        return StepOutcome.failed(ErrorCode.WORKFLOW_INVALID, False)


@dataclass
class AuditFake:
    entries: list[AuditEntry]

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        del session
        self.entries.append(entry)
        return len(self.entries)


@dataclass
class IssuesFake:
    async def record_scan(self, session: AsyncSession, scan: ResourceScan) -> RecordOutcome:
        del session, scan
        return RecordOutcome.empty()

    async def record_external(self, session: AsyncSession, scan: ExternalScan) -> RecordOutcome:
        del session, scan
        return RecordOutcome.empty()

    async def add_history(self, session: AsyncSession, entry: IssueHistoryEntry) -> None:
        del session, entry

    async def resolve_unchecked(
        self, session: AsyncSession, collector_config_id: str, checked_before: datetime
    ) -> int:
        del session, collector_config_id, checked_before
        return 0


@dataclass
class IssueReaderFake:
    async def get(self, session: AsyncSession, issue_id: str) -> IssueSnapshot | None:
        del session, issue_id
        return None

    async def open_resource_refs(
        self, session: AsyncSession, collector_config_id: str
    ) -> tuple[OpenResourceRef, ...]:
        del session, collector_config_id
        return ()


@dataclass
class IndexerFake:
    indexed: list[str]

    async def index_run(self, run_id: str) -> None:
        self.indexed.append(run_id)

    async def index_issue(self, issue_id: str) -> None:
        del issue_id

    async def index_finding(self, finding_id: str) -> None:
        del finding_id


@dataclass
class ConfigFake:
    async def get(self, session: AsyncSession, collector_config_id: str) -> CollectorConfig | None:
        del session, collector_config_id
        return None

    async def get_by_name(self, session: AsyncSession, name: str) -> CollectorConfig | None:
        del session, name
        return None


@dataclass
class EmptyProvider:
    async def tick(self, session: AsyncSession, now: datetime) -> int:
        del session, now
        return 0


@pytest.mark.asyncio
async def test_queue_idempotency_due_claim_and_expired_lease() -> None:
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))
    queue: SqlTaskQueue = SqlTaskQueue(clock)
    try:
        async with harness.sessions.begin() as session:
            spec: TaskSpec = TaskSpec(
                EngineTaskKind.ADVANCE_STEP,
                "step-1",
                json_object(()),
                clock.now() + timedelta(seconds=5),
                "advance:step-1:1",
            )
            assert await queue.enqueue(session, spec)
            assert not await queue.enqueue(session, spec)
            assert (
                await queue.claim(
                    session, "worker-1", (EngineTaskKind.ADVANCE_STEP,), 2, timedelta(seconds=10)
                )
                == []
            )
        clock.advance(timedelta(seconds=5))
        async with harness.sessions.begin() as session:
            claimed: list[ClaimedTask] = await queue.claim(
                session, "worker-1", (EngineTaskKind.ADVANCE_STEP,), 2, timedelta(seconds=10)
            )
            assert len(claimed) == 1
            assert claimed[0].attempt == 1
        clock.advance(timedelta(seconds=11))
        async with harness.sessions.begin() as session:
            assert await queue.reclaim_expired(session) == 1
            row: EngineTaskRow | None = await session.scalar(select(EngineTaskRow))
            assert row is not None and row.status == EngineTaskStatus.READY.value
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_linear_workflow_persists_transitions_and_terminal_status() -> None:
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))
    audit: AuditFake = AuditFake([])
    indexer: IndexerFake = IndexerFake([])
    repository: WorkflowRepository = WorkflowRepository()
    queue: SqlTaskQueue = SqlTaskQueue(clock)
    redactor: Redactor = Redactor.default()
    service: WorkflowService = WorkflowService(redactor, ConfigFake(), audit, clock, repository)
    executors: ExecutorRegistry = ExecutorRegistry()
    executors.register(ConditionExecutor())
    executors.register(EndExecutor())
    engine: RunEngine = RunEngine(
        service,
        executors,
        queue,
        IssueReaderFake(),
        IssuesFake(),
        indexer,
        redactor,
        audit,
        clock,
        repository,
    )
    registry: TaskHandlerRegistry = TaskHandlerRegistry()
    registry.register(AdvanceStepHandler(harness.sessions, engine))
    registry.register(TimerFiredHandler(harness.sessions, engine))
    worker: EngineWorker = EngineWorker(
        harness.sessions,
        queue,
        registry,
        Scheduler(harness.sessions, (EmptyProvider(),), clock),
        clock,
        "test-worker",
    )
    source: str = """apiVersion: telescreen.workflow/v1
kind: Workflow
name: linear
start: begin
steps:
  - id: begin
    type: condition
    default: finish
  - id: finish
    type: end
    result: succeeded
"""
    try:
        async with harness.sessions.begin() as session:
            definition: WorkflowDefinitionRow = await service.upload(
                session, SourceFormat.YAML, source, "member:test"
            )
            await service.activate(session, "linear", definition.version)
            run_id: str = await engine.start_run(session, definition.id, None, RunTrigger.MANUAL)
        assert await worker.run_until_idle() == 2
        async with harness.sessions() as session:
            run: WorkflowRunRow | None = await repository.run(session, run_id)
            assert run is not None and run.status == RunStatus.SUCCEEDED.value
            assert len(await repository.transitions(session, run_id)) == 2
        assert indexer.indexed == [run_id, run_id]
    finally:
        await harness.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("join_mode", ("all", "any"))
async def test_parallel_waits_resume_and_join_once(join_mode: str) -> None:
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))
    audit: AuditFake = AuditFake([])
    indexer: IndexerFake = IndexerFake([])
    repository: WorkflowRepository = WorkflowRepository()
    queue: SqlTaskQueue = SqlTaskQueue(clock)
    redactor: Redactor = Redactor.default()
    service: WorkflowService = WorkflowService(redactor, ConfigFake(), audit, clock, repository)
    executors: ExecutorRegistry = ExecutorRegistry()
    executors.register(ParallelExecutor())
    executors.register(WaitExecutor())
    executors.register(JoinExecutor())
    executors.register(EndExecutor())
    engine: RunEngine = RunEngine(
        service,
        executors,
        queue,
        IssueReaderFake(),
        IssuesFake(),
        indexer,
        redactor,
        audit,
        clock,
        repository,
    )
    registry: TaskHandlerRegistry = TaskHandlerRegistry()
    registry.register(AdvanceStepHandler(harness.sessions, engine))
    registry.register(TimerFiredHandler(harness.sessions, engine))
    worker: EngineWorker = EngineWorker(
        harness.sessions,
        queue,
        registry,
        Scheduler(harness.sessions, (EmptyProvider(),), clock),
        clock,
        "parallel-worker",
        concurrency=1,
    )
    source: str = f"""apiVersion: telescreen.workflow/v1
kind: Workflow
name: parallel
start: fork
steps:
  - id: fork
    type: parallel
    branches: [left, right]
    join: joined
  - id: left
    type: wait
    duration: PT10S
    next: joined
  - id: right
    type: wait
    duration: PT10S
    next: joined
  - id: joined
    type: join
    mode: {join_mode}
    next: finish
  - id: finish
    type: end
    result: succeeded
"""
    try:
        async with harness.sessions.begin() as session:
            definition: WorkflowDefinitionRow = await service.upload(
                session, SourceFormat.YAML, source, "member:test"
            )
            run_id: str = await engine.start_run(session, definition.id, None, RunTrigger.MANUAL)
        await worker.run_until_idle()
        async with harness.sessions() as session:
            run: WorkflowRunRow | None = await repository.run(session, run_id)
            assert run is not None and run.status == RunStatus.WAITING.value
            assert run.finished_at is None
        clock.advance(timedelta(seconds=10))
        await worker.run_until_idle()
        async with harness.sessions() as session:
            run = await repository.run(session, run_id)
            assert run is not None and run.status == RunStatus.SUCCEEDED.value
            join_visits: list[StepRunRow] = [
                step for step in await repository.steps(session, run_id) if step.step_id == "joined"
            ]
            if join_mode == "all":
                assert len(join_visits) == 2
            else:
                assert 1 <= len(join_visits) <= 2
            assert sum(step.status == "succeeded" for step in join_visits) == 1
            if join_mode == "any":
                all_steps: Sequence[StepRunRow] = await repository.steps(session, run_id)
                assert any(step.status == "cancelled" for step in all_steps)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_workflow_source_with_secret_comment_is_rejected() -> None:
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))
    audit: AuditFake = AuditFake([])
    service: WorkflowService = WorkflowService(
        Redactor.default(), ConfigFake(), audit, clock, WorkflowRepository()
    )
    source: str = (
        "# sample credential: "
        + github_token().value
        + "\napiVersion: telescreen.workflow/v1\nkind: Workflow\nname: safe\n"
        + "start: finish\nsteps:\n  - id: finish\n    type: end\n    result: succeeded\n"
    )
    try:
        errors: Sequence[ValidationIssue] = await service.validate(SourceFormat.YAML, source)
        assert len(errors) == 1
        assert errors[0].code == "raw_value_detected"
        assert errors[0].path == ""
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_retry_redacts_output_and_duplicate_advance_is_a_noop() -> None:
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))
    audit: AuditFake = AuditFake([])
    indexer: IndexerFake = IndexerFake([])
    repository: WorkflowRepository = WorkflowRepository()
    queue: SqlTaskQueue = SqlTaskQueue(clock)
    redactor: Redactor = Redactor.default()
    service: WorkflowService = WorkflowService(redactor, ConfigFake(), audit, clock, repository)
    sample: str = github_token().value
    retry_executor: RetryOutputExecutor = RetryOutputExecutor(sample)
    executors: ExecutorRegistry = ExecutorRegistry()
    executors.register(retry_executor)
    executors.register(EndExecutor())
    engine: RunEngine = RunEngine(
        service,
        executors,
        queue,
        IssueReaderFake(),
        IssuesFake(),
        indexer,
        redactor,
        audit,
        clock,
        repository,
    )
    registry: TaskHandlerRegistry = TaskHandlerRegistry()
    advance_handler: AdvanceStepHandler = AdvanceStepHandler(harness.sessions, engine)
    registry.register(advance_handler)
    registry.register(TimerFiredHandler(harness.sessions, engine))
    worker: EngineWorker = EngineWorker(
        harness.sessions,
        queue,
        registry,
        Scheduler(harness.sessions, (EmptyProvider(),), clock),
        clock,
        "retry-worker",
        concurrency=1,
    )
    source: str = """apiVersion: telescreen.workflow/v1
kind: Workflow
name: retry
start: begin
steps:
  - id: begin
    type: condition
    default: finish
  - id: finish
    type: end
    result: succeeded
"""
    try:
        async with harness.sessions.begin() as session:
            definition: WorkflowDefinitionRow = await service.upload(
                session, SourceFormat.YAML, source, "member:test"
            )
            run_id: str = await engine.start_run(session, definition.id, None, RunTrigger.MANUAL)
        assert await worker.run_until_idle() == 1
        assert retry_executor.attempts == 1
        clock.advance(timedelta(seconds=10))
        await worker.run_until_idle()
        first_step_id: str
        transitions_before: int
        async with harness.sessions() as session:
            run: WorkflowRunRow | None = await repository.run(session, run_id)
            assert run is not None and run.status == RunStatus.SUCCEEDED.value
            steps: Sequence[StepRunRow] = await repository.steps(session, run_id)
            first_step: StepRunRow = next(step for step in steps if step.step_id == "begin")
            first_step_id = first_step.id
            assert first_step.output_json is not None
            assert sample not in first_step.output_json
            assert sample not in run.context_json
            transitions_before = len(await repository.transitions(session, run_id))
        duplicate: ClaimedTask = ClaimedTask(
            "duplicate-delivery",
            EngineTaskKind.ADVANCE_STEP,
            first_step_id,
            json_object((("step_run_id", json_str(first_step_id)),)),
            1,
            5,
            "advance:duplicate",
        )
        await engine.advance_task(harness.sessions, duplicate)
        async with harness.sessions() as session:
            transitions_after: int = len(await repository.transitions(session, run_id))
            assert transitions_after == transitions_before
        assert retry_executor.attempts == 2
    finally:
        await harness.close()
