"""Durable workflow run state machine and queue task handlers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock
from telescreen.core.ids import new_id
from telescreen.core.json import (
    JsonKind,
    JsonValue,
    dump_json,
    json_object,
    json_str,
    parse_json,
)
from telescreen.core.vocabulary import (
    ActorKind,
    EngineTaskKind,
    ErrorCode,
    IssueHistoryKind,
    RunStatus,
    RunTrigger,
    StepRunStatus,
    TransitionKind,
)
from telescreen.db.models import (
    HumanTaskRow,
    StepRunRow,
    TransitionRow,
    WorkflowDefinitionRow,
    WorkflowRunRow,
)
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.issues import IssueHistoryEntry, IssueReader, IssueRecorder, IssueSnapshot
from telescreen.ports.search import SearchIndexer
from telescreen.ports.workflow import (
    ClaimedTask,
    RunSignaler,
    StepSignal,
    StepSignalKind,
    TaskResult,
    TaskSpec,
)
from telescreen.workflow.dsl.context import RunContext
from telescreen.workflow.dsl.loader import compiled_from_json
from telescreen.workflow.dsl.model import (
    CompiledWorkflow,
    EndStep,
    JoinMode,
    JoinStep,
    ParallelStep,
    SendBackStep,
    StepSpec,
    SubWorkflowStep,
)
from telescreen.workflow.engine.context import initial_context, latest_context, redact_json
from telescreen.workflow.engine.definitions import WorkflowService
from telescreen.workflow.engine.repository import WorkflowRepository
from telescreen.workflow.executors import ExecutorRegistry
from telescreen.workflow.queue.sql_queue import SqlTaskQueue
from telescreen.workflow.step_api import StepContext, StepExecutor, StepOutcome, StepOutcomeKind


@dataclass
class RunEngine(RunSignaler):
    """Create and advance durable workflow runs inside caller transactions."""

    definitions: WorkflowService
    executors: ExecutorRegistry
    queue: SqlTaskQueue
    issues_reader: IssueReader
    issues: IssueRecorder
    indexer: SearchIndexer
    redactor: Redactor
    audit: AuditLog
    clock: Clock
    repository: WorkflowRepository

    async def start_run(
        self,
        session: AsyncSession,
        definition_id: str,
        issue_id: str | None,
        trigger: RunTrigger,
        parent_step_run_id: str | None = None,
    ) -> str:
        """Persist a run, its start transition, first step, and first queue task."""
        definition: WorkflowDefinitionRow | None = await self.repository.definition(
            session, definition_id
        )
        if definition is None:
            from telescreen.core.errors import TelescreenError

            raise TelescreenError(ErrorCode.NOT_FOUND, "workflow definition not found")
        workflow: CompiledWorkflow = compiled_from_json(parse_json(definition.compiled_json))
        run_id: str = new_id()
        now: datetime = self.clock.now()
        issue: IssueSnapshot | None = (
            await self.issues_reader.get(session, issue_id) if issue_id is not None else None
        )
        context: RunContext = initial_context(run_id, workflow.name, now.isoformat(), issue)
        run: WorkflowRunRow = WorkflowRunRow(
            id=run_id,
            definition_id=definition.id,
            issue_id=issue_id,
            parent_step_run_id=parent_step_run_id,
            status=RunStatus.RUNNING.value,
            trigger=trigger.value,
            context_json=dump_json(context.to_json()),
            lock_version=0,
            started_at=now,
        )
        session.add(run)
        step_run_id: str = await self._create_step(session, run, workflow.start, None, 1)
        session.add(
            TransitionRow(
                id=new_id(),
                run_id=run_id,
                from_step_run_id=None,
                to_step_id=workflow.start,
                kind=TransitionKind.START.value,
                at=now,
            )
        )
        await self._enqueue_advance(session, step_run_id, None, now)
        await self.audit.append(
            session,
            AuditEntry(
                ActorKind.SYSTEM,
                "system:workflow",
                "run.started",
                "workflow_run",
                run_id,
                "Workflow run started",
                json_object((("workflow", json_str(workflow.name)),)),
            ),
        )
        if issue_id is not None:
            await self.issues.add_history(
                session,
                IssueHistoryEntry(
                    issue_id,
                    IssueHistoryKind.WORKFLOW_STARTED,
                    "system:workflow",
                    now,
                    workflow.name,
                ),
            )
        return run_id

    async def start_child(self, ctx: StepContext, step: SubWorkflowStep) -> str:
        """Start a child workflow linked to its waiting parent step."""
        async with ctx.session.begin_nested():
            definition: WorkflowDefinitionRow | None = await self.repository.definition_by_name(
                ctx.session, step.workflow, step.version
            )
            if definition is None:
                from telescreen.core.errors import TelescreenError

                raise TelescreenError(ErrorCode.NOT_FOUND, "child workflow not found")
            return await self.start_run(
                ctx.session,
                definition.id,
                ctx.issue_id,
                RunTrigger.SUB_WORKFLOW,
                ctx.step_run_id,
            )

    async def signal(self, session: AsyncSession, step_run_id: str, signal: StepSignal) -> None:
        """Resume a waiting step once for each signal kind and visit."""
        step_run: StepRunRow | None = await self.repository.step_run(session, step_run_id)
        if step_run is None or step_run.status != StepRunStatus.WAITING.value:
            return
        await self._enqueue_advance(
            session,
            step_run_id,
            signal,
            self.clock.now(),
            f"signal:{step_run_id}:{signal.kind.value}:{step_run.visit}",
        )

    async def cancel_run(self, session: AsyncSession, run_id: str, actor_ref: str) -> None:
        """Cancel all pending work and human tasks for a workflow run."""
        run: WorkflowRunRow | None = await self.repository.run(session, run_id)
        if run is None or run.status in (
            RunStatus.SUCCEEDED.value,
            RunStatus.FAILED.value,
            RunStatus.CANCELLED.value,
        ):
            return
        steps: Sequence[StepRunRow] = await self.repository.steps(session, run_id)
        for step in steps:
            if step.status in (StepRunStatus.READY.value, StepRunStatus.WAITING.value):
                step.status = StepRunStatus.CANCELLED.value
                await self.queue.cancel_by_ref(session, step.id)
        tasks: Sequence[HumanTaskRow] = await self.repository.human_tasks(session, run_id)
        for task in tasks:
            if task.status == "open":
                await self.queue.cancel_by_ref(session, task.id)
                task.status = "cancelled"
        run.status = RunStatus.CANCELLED.value
        run.finished_at = self.clock.now()
        run.lock_version += 1
        await self.audit.append(
            session,
            AuditEntry(
                ActorKind.MEMBER,
                actor_ref,
                "run.cancelled",
                "workflow_run",
                run_id,
                "Workflow run cancelled",
                json_object(()),
            ),
        )

    async def advance_task(
        self,
        sessions: async_sessionmaker[AsyncSession],
        task: ClaimedTask,
    ) -> TaskResult:
        """Execute one queued step and index the run only after commit."""
        payload_step: JsonValue | None = task.payload.get("step_run_id")
        step_run_id: str | None = payload_step.as_str() if payload_step is not None else None
        if step_run_id is None:
            return TaskResult.dead(ErrorCode.WORKFLOW_INVALID)
        run_id: str | None = None
        async with sessions.begin() as session:
            step_run: StepRunRow | None = await self.repository.step_run(session, step_run_id)
            if step_run is None:
                return TaskResult.done()
            run_id = step_run.run_id
            expected: str = (
                StepRunStatus.WAITING.value
                if task.payload.get("signal") is not None
                else StepRunStatus.READY.value
            )
            if step_run.status != expected:
                return TaskResult.done()
            run: WorkflowRunRow | None = await self.repository.run(session, step_run.run_id)
            if run is None or run.status in (
                RunStatus.CANCELLED.value,
                RunStatus.SUCCEEDED.value,
                RunStatus.FAILED.value,
            ):
                return TaskResult.done()
            definition: WorkflowDefinitionRow | None = await self.repository.definition(
                session, run.definition_id
            )
            if definition is None:
                return TaskResult.dead(ErrorCode.WORKFLOW_INVALID)
            workflow: CompiledWorkflow = compiled_from_json(parse_json(definition.compiled_json))
            await self._advance(session, run, step_run, workflow, task.payload)
        if run_id is not None:
            await self.indexer.index_run(run_id)
        return TaskResult.done()

    async def fail_dead_step(self, session: AsyncSession, task: ClaimedTask) -> None:
        """Mark a step failed after the queue exhausts task retries."""
        step_run: StepRunRow | None = await self.repository.step_run(session, task.ref_id)
        if step_run is not None:
            run: WorkflowRunRow | None = await self.repository.run(session, step_run.run_id)
            if run is None:
                return
            definition: WorkflowDefinitionRow | None = await self.repository.definition(
                session, run.definition_id
            )
            if definition is None:
                run.status = RunStatus.FAILED.value
                run.finished_at = self.clock.now()
                run.lock_version += 1
                await self._notify_parent_finished(session, run)
                return
            workflow: CompiledWorkflow = compiled_from_json(parse_json(definition.compiled_json))
            step: StepSpec = workflow.step(step_run.step_id)
            step_run.status = StepRunStatus.RUNNING.value
            await self._failed(session, run, step_run, step, ErrorCode.TASK_DEAD, False)

    async def _advance(
        self,
        session: AsyncSession,
        run: WorkflowRunRow,
        step_run: StepRunRow,
        workflow: CompiledWorkflow,
        payload: JsonValue,
    ) -> None:
        """Execute and apply one workflow step outcome transactionally."""
        step: StepSpec = workflow.step(step_run.step_id)
        context: RunContext = RunContext.from_json(parse_json(run.context_json))
        signal: StepSignal | None = _read_signal(payload.get("signal"))
        step_run.status = StepRunStatus.RUNNING.value
        step_run.started_at = step_run.started_at or self.clock.now()
        executor: StepExecutor = self.executors.get(step.step_type)
        ctx: StepContext = StepContext(
            session,
            run.id,
            step_run.id,
            step,
            step_run.visit,
            step_run.attempt,
            context,
            run.issue_id,
            self.clock.now(),
        )
        outcome: StepOutcome = (
            await executor.resume(ctx, signal)
            if signal is not None
            else await executor.execute(ctx)
        )
        if outcome.kind is StepOutcomeKind.WAITING:
            step_run.status = StepRunStatus.WAITING.value
            active_count: int = int(
                await session.scalar(
                    select(func.count(StepRunRow.id)).where(
                        StepRunRow.run_id == run.id,
                        StepRunRow.id != step_run.id,
                        StepRunRow.status.in_(
                            (StepRunStatus.READY.value, StepRunStatus.RUNNING.value)
                        ),
                    )
                )
                or 0
            )
            run.status = RunStatus.WAITING.value if active_count == 0 else RunStatus.RUNNING.value
            if outcome.timer_at is not None:
                await self.queue.enqueue(
                    session,
                    TaskSpec(
                        EngineTaskKind.TIMER_FIRED,
                        step_run.id,
                        json_object(
                            (
                                ("step_run_id", json_str(step_run.id)),
                                ("signal", _signal_json(StepSignal(StepSignalKind.TIMER_FIRED))),
                            )
                        ),
                        outcome.timer_at,
                        f"timer:{step_run.id}:{step_run.visit}",
                    ),
                )
            if isinstance(step, JoinStep):
                await self._complete_join_arrivals(session, run, step_run, step)
            return
        if outcome.kind is StepOutcomeKind.FAILED:
            await self._failed(session, run, step_run, step, outcome.error_code, outcome.retryable)
            return
        safe_output: JsonValue = redact_json(outcome.output, self.redactor)
        step_run.status = StepRunStatus.SUCCEEDED.value
        step_run.output_json = dump_json(safe_output)
        step_run.outcome = "completed"
        step_run.finished_at = self.clock.now()
        context = latest_context(context, step.id, "completed", safe_output)
        run.context_json = dump_json(context.to_json())
        run.lock_version += 1
        if isinstance(step, EndStep):
            run.status = step.result.value
            run.finished_at = self.clock.now()
            await self._notify_parent_finished(session, run)
            return
        if isinstance(step, ParallelStep):
            await self._fork(session, run, step_run, step)
            return
        target: str | None = outcome.route or step.next
        transition_kind: TransitionKind = TransitionKind.NEXT
        reason: str | None = None
        if isinstance(step, SendBackStep):
            count: int = int(
                await session.scalar(
                    select(func.count(TransitionRow.id)).where(
                        TransitionRow.run_id == run.id,
                        TransitionRow.kind == TransitionKind.SEND_BACK.value,
                    )
                )
                or 0
            )
            if count >= step.max_send_backs:
                target = step.on_exhausted
            else:
                target = step.to
                transition_kind = TransitionKind.SEND_BACK
                reason_value: JsonValue | None = safe_output.get("reason")
                reason = reason_value.as_str() if reason_value is not None else None
        if target is None:
            run.status = RunStatus.FAILED.value
            run.finished_at = self.clock.now()
            return
        previous_count: int = int(
            await session.scalar(
                select(func.count(StepRunRow.id)).where(
                    StepRunRow.run_id == run.id,
                    StepRunRow.step_id == target,
                )
            )
            or 0
        )
        if previous_count >= workflow.defaults.max_step_visits:
            run.status = RunStatus.FAILED.value
            run.finished_at = self.clock.now()
            step_run.error_code = ErrorCode.LOOP_LIMIT_EXCEEDED.value
            return
        next_step_run_id: str = await self._create_step(
            session, run, target, step_run.branch_token, previous_count + 1
        )
        session.add(
            TransitionRow(
                id=new_id(),
                run_id=run.id,
                from_step_run_id=step_run.id,
                to_step_id=target,
                kind=transition_kind.value,
                reason=reason,
                at=self.clock.now(),
            )
        )
        run.status = RunStatus.RUNNING.value
        await self._enqueue_advance(session, next_step_run_id, None, self.clock.now())

    async def _fork(
        self, session: AsyncSession, run: WorkflowRunRow, source: StepRunRow, step: ParallelStep
    ) -> None:
        """Create one durable branch per configured parallel entry point."""
        if not step.branches:
            run.status = RunStatus.FAILED.value
            run.finished_at = self.clock.now()
            source.error_code = ErrorCode.WORKFLOW_INVALID.value
            return
        source.output_json = dump_json(json_object((("join", json_str(step.join)),)))
        for index, target in enumerate(step.branches):
            token: str = f"{source.id}:{index}"
            visit: int = (
                int(
                    await session.scalar(
                        select(func.count(StepRunRow.id)).where(
                            StepRunRow.run_id == run.id,
                            StepRunRow.step_id == target,
                            StepRunRow.branch_token == token,
                        )
                    )
                    or 0
                )
                + 1
            )
            branch_id: str = await self._create_step(session, run, target, token, visit)
            await self._enqueue_advance(session, branch_id, None, self.clock.now())
            session.add(
                TransitionRow(
                    id=new_id(),
                    run_id=run.id,
                    from_step_run_id=source.id,
                    to_step_id=target,
                    kind=TransitionKind.NEXT.value,
                    at=self.clock.now(),
                )
            )
        run.status = RunStatus.RUNNING.value

    async def _complete_join_arrivals(
        self, session: AsyncSession, run: WorkflowRunRow, current: StepRunRow, step: JoinStep
    ) -> None:
        """Release one join continuation after all, or any, branch has arrived."""
        token: str = current.branch_token or ""
        fork_id: str = token.split(":", maxsplit=1)[0]
        fork: StepRunRow | None = await self.repository.step_run(session, fork_id)
        if fork is None:
            run.status = RunStatus.FAILED.value
            run.finished_at = self.clock.now()
            current.error_code = ErrorCode.WORKFLOW_INVALID.value
            return
        workflow_definition: WorkflowDefinitionRow | None = await self.repository.definition(
            session, run.definition_id
        )
        if workflow_definition is None:
            run.status = RunStatus.FAILED.value
            run.finished_at = self.clock.now()
            return
        workflow: CompiledWorkflow = compiled_from_json(
            parse_json(workflow_definition.compiled_json)
        )
        fork_spec: StepSpec = workflow.step(fork.step_id)
        if not isinstance(fork_spec, ParallelStep):
            run.status = RunStatus.FAILED.value
            run.finished_at = self.clock.now()
            return
        arrivals: list[StepRunRow] = list(
            (
                await session.scalars(
                    select(StepRunRow)
                    .where(
                        StepRunRow.run_id == run.id,
                        StepRunRow.step_id == step.id,
                        StepRunRow.branch_token.like(f"{fork_id}:%"),
                        StepRunRow.status == StepRunStatus.WAITING.value,
                    )
                    .order_by(StepRunRow.id)
                )
            ).all()
        )
        if step.mode is JoinMode.ALL and len(arrivals) < len(fork_spec.branches):
            run.status = RunStatus.RUNNING.value
            return
        leader: StepRunRow = arrivals[0]
        for arrival in arrivals[1:]:
            arrival.status = StepRunStatus.CANCELLED.value
            await self.queue.cancel_by_ref(session, arrival.id)
        if step.mode is JoinMode.ANY:
            steps: Sequence[StepRunRow] = await self.repository.steps(session, run.id)
            for other in steps:
                if (
                    other.branch_token is not None
                    and other.branch_token.startswith(f"{fork_id}:")
                    and other.id != leader.id
                    and other.status in (StepRunStatus.READY.value, StepRunStatus.WAITING.value)
                ):
                    other.status = StepRunStatus.CANCELLED.value
                    await self.queue.cancel_by_ref(session, other.id)
        run.status = RunStatus.WAITING.value
        await self.signal(session, leader.id, StepSignal(StepSignalKind.BRANCH_ARRIVED))

    async def _notify_parent_finished(self, session: AsyncSession, run: WorkflowRunRow) -> None:
        """Queue a child completion signal after the run reaches an end step."""
        if run.parent_step_run_id is None:
            return
        await self.queue.enqueue(
            session,
            TaskSpec(
                EngineTaskKind.CHILD_RUN_FINISHED,
                run.id,
                json_object(()),
                self.clock.now(),
                f"child:{run.id}",
            ),
        )

    async def _failed(
        self,
        session: AsyncSession,
        run: WorkflowRunRow,
        step_run: StepRunRow,
        step: StepSpec,
        error_code: ErrorCode | None,
        retryable: bool,
    ) -> None:
        """Persist a retry or route a failed step through on_error."""
        step_run.error_code = (error_code or ErrorCode.INTERNAL_ERROR).value
        if retryable and step_run.attempt < 5:
            step_run.status = StepRunStatus.READY.value
            step_run.attempt += 1
            delay: timedelta = timedelta(seconds=10 * (2 ** (step_run.attempt - 2)))
            await self._enqueue_advance(
                session,
                step_run.id,
                None,
                self.clock.now() + delay,
                f"advance:{step_run.id}:{step_run.attempt}",
            )
            return
        step_run.status = StepRunStatus.FAILED.value
        step_run.finished_at = self.clock.now()
        target: str | None = step.on_error
        if target is None:
            run.status = RunStatus.FAILED.value
            run.finished_at = self.clock.now()
            run.lock_version += 1
            await self._notify_parent_finished(session, run)
            return
        next_visit: int = (
            int(
                await session.scalar(
                    select(func.count(StepRunRow.id)).where(
                        StepRunRow.run_id == run.id,
                        StepRunRow.step_id == target,
                        StepRunRow.branch_token == step_run.branch_token,
                    )
                )
                or 0
            )
            + 1
        )
        next_step_run_id: str = await self._create_step(
            session, run, target, step_run.branch_token, next_visit
        )
        session.add(
            TransitionRow(
                id=new_id(),
                run_id=run.id,
                from_step_run_id=step_run.id,
                to_step_id=target,
                kind=TransitionKind.ERROR.value,
                reason=step_run.error_code,
                at=self.clock.now(),
            )
        )
        run.status = RunStatus.RUNNING.value
        run.lock_version += 1
        await self._enqueue_advance(session, next_step_run_id, None, self.clock.now())

    async def _create_step(
        self,
        session: AsyncSession,
        run: WorkflowRunRow,
        step_id: str,
        branch_token: str | None,
        visit: int,
    ) -> str:
        """Persist a ready step visit and return its id."""
        step_run_id: str = new_id()
        session.add(
            StepRunRow(
                id=step_run_id,
                run_id=run.id,
                step_id=step_id,
                visit=visit,
                branch_token=branch_token,
                status=StepRunStatus.READY.value,
                attempt=1,
                input_json=run.context_json,
            )
        )
        return step_run_id

    async def _enqueue_advance(
        self,
        session: AsyncSession,
        step_run_id: str,
        signal: StepSignal | None,
        available_at: datetime,
        idempotency_key: str | None = None,
    ) -> None:
        """Enqueue one idempotent step advance or resume."""
        members: list[tuple[str, JsonValue]] = [("step_run_id", json_str(step_run_id))]
        if signal is not None:
            members.append(("signal", _signal_json(signal)))
        key: str = idempotency_key or f"advance:{step_run_id}:1"
        await self.queue.enqueue(
            session,
            TaskSpec(
                EngineTaskKind.ADVANCE_STEP,
                step_run_id,
                json_object(tuple(members)),
                available_at,
                key,
            ),
        )


@dataclass
class AdvanceStepHandler:
    """Handle both normal advances and timer-driven resumes."""

    sessions: async_sessionmaker[AsyncSession]
    engine: RunEngine
    kind: EngineTaskKind = EngineTaskKind.ADVANCE_STEP

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """Delegate a claimed task to the durable run engine."""
        return await self.engine.advance_task(self.sessions, task)

    async def on_dead(self, task: ClaimedTask) -> None:
        """Mark a step failed after all queue attempts are exhausted."""
        async with self.sessions.begin() as session:
            await self.engine.fail_dead_step(session, task)


@dataclass
class TimerFiredHandler:
    """Resume a timed step when its durable timer task is due."""

    sessions: async_sessionmaker[AsyncSession]
    engine: RunEngine
    kind: EngineTaskKind = EngineTaskKind.TIMER_FIRED

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """Delegate timer delivery to the same idempotent advance path."""
        return await self.engine.advance_task(self.sessions, task)


def _signal_json(signal: StepSignal) -> JsonValue:
    """Serialize a safe engine signal."""
    return json_object(
        (
            ("kind", json_str(signal.kind.value)),
            ("outcome", json_str(signal.outcome or "")),
            ("data", signal.data),
        )
    )


def _read_signal(value: JsonValue | None) -> StepSignal | None:
    """Parse a stored signal without accepting arbitrary task payloads."""
    if value is None or value.kind is not JsonKind.OBJECT:
        return None
    kind_value: JsonValue | None = value.get("kind")
    kind_text: str | None = kind_value.as_str() if kind_value is not None else None
    if kind_text is None:
        return None
    try:
        kind: StepSignalKind = StepSignalKind(kind_text)
    except ValueError:
        return None
    outcome_value: JsonValue | None = value.get("outcome")
    outcome: str | None = outcome_value.as_str() if outcome_value is not None else None
    if outcome == "":
        outcome = None
    data: JsonValue = value.get("data") or parse_json("null")
    return StepSignal(kind, outcome, data)
