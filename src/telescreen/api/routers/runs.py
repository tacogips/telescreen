"""Durable workflow run inspection and cancellation APIs."""

from dataclasses import asdict, dataclass
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import Select, select

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.context import ApiContext
from telescreen.core.json import parse_json
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import RunStatus
from telescreen.db.models import (
    HumanTaskRow,
    StepRunRow,
    TransitionRow,
    WorkflowDefinitionRow,
    WorkflowRunRow,
)
from telescreen.workflow.dsl.loader import CompiledWorkflow, compiled_from_json
from telescreen.workflow.dsl.model import EvidenceUploadStep, HumanTaskStep, StepSpec
from telescreen.workflow.engine.engine import RunEngine
from telescreen.workflow.engine.repository import WorkflowRepository


@dataclass(frozen=True)
class RunsRouterDeps:
    """Dependencies for workflow run endpoints."""

    ctx: ApiContext
    engine: RunEngine
    repository: WorkflowRepository


@dataclass(frozen=True)
class _RunSummary:
    id: str
    definition_id: str
    issue_id: str | None
    status: str
    trigger: str
    started_at: str
    finished_at: str | None


@dataclass(frozen=True)
class _StepResponse:
    id: str
    step_id: str
    visit: int
    branch_token: str | None
    status: str
    attempt: int
    input_json: str
    output_json: str | None
    error_code: str | None


@dataclass(frozen=True)
class _TransitionResponse:
    from_step_run_id: str | None
    to_step_id: str
    kind: str
    reason: str | None
    at: str


@dataclass(frozen=True)
class _HumanResponse:
    id: str
    step_run_id: str
    kind: str
    title: str
    assignee_ref: str
    status: str
    outcome: str | None
    outcomes: tuple[str, ...]


def build_runs_router(deps: RunsRouterDeps) -> APIRouter:
    """Build run inspection and cancellation routes."""
    router: APIRouter = APIRouter(tags=["runs"])

    @router.get("/api/runs")
    async def list_runs(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.RUNS_READ))
        ],
        status: RunStatus | None = None,
        workflow: str | None = None,
        issue_id: str | None = None,
    ) -> JSONResponse:
        del principal
        async with deps.ctx.sessions() as session:
            query: Select[tuple[WorkflowRunRow]] = select(WorkflowRunRow)
            if status is not None:
                query = query.where(WorkflowRunRow.status == status.value)
            if issue_id is not None:
                query = query.where(WorkflowRunRow.issue_id == issue_id)
            if workflow is not None:
                query = query.join(
                    WorkflowDefinitionRow,
                    WorkflowDefinitionRow.id == WorkflowRunRow.definition_id,
                ).where(WorkflowDefinitionRow.name == workflow)
            rows: list[WorkflowRunRow] = list(
                (await session.scalars(query.order_by(WorkflowRunRow.started_at.desc()))).all()
            )
        return JSONResponse(content={"items": [asdict(_run_summary(row)) for row in rows]})

    @router.get("/api/runs/{run_id}")
    async def get_run(
        run_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.RUNS_READ))
        ],
    ) -> JSONResponse:
        del principal
        async with deps.ctx.sessions() as session:
            run: WorkflowRunRow | None = await deps.repository.run(session, run_id)
            if run is None:
                from telescreen.core.errors import TelescreenError
                from telescreen.core.vocabulary import ErrorCode

                raise TelescreenError(ErrorCode.NOT_FOUND, "workflow run not found")
            definition: WorkflowDefinitionRow | None = await deps.repository.definition(
                session, run.definition_id
            )
            if definition is None:
                from telescreen.core.errors import TelescreenError
                from telescreen.core.vocabulary import ErrorCode

                raise TelescreenError(ErrorCode.NOT_FOUND, "workflow definition not found")
            workflow: CompiledWorkflow = compiled_from_json(parse_json(definition.compiled_json))
            steps: list[StepRunRow] = list(await deps.repository.steps(session, run_id))
            transitions: list[TransitionRow] = list(
                await deps.repository.transitions(session, run_id)
            )
            human_tasks: list[HumanTaskRow] = list(
                await deps.repository.human_tasks(session, run_id)
            )
        return JSONResponse(
            content={
                "run": asdict(_run_summary(run)),
                "steps": [asdict(_step_json(row)) for row in steps],
                "transitions": [asdict(_transition_json(row)) for row in transitions],
                "human_tasks": [asdict(_human_json(row, steps, workflow)) for row in human_tasks],
            }
        )

    @router.post("/api/runs/{run_id}/cancel")
    async def cancel_run(
        run_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.RUNS_WRITE))
        ],
    ) -> JSONResponse:
        async with deps.ctx.sessions.begin() as session:
            await deps.engine.cancel_run(session, run_id, principal.actor_ref())
        return JSONResponse(content={"run_id": run_id, "status": "cancelled"})

    return router


def _run_summary(row: WorkflowRunRow) -> _RunSummary:
    return _RunSummary(
        row.id,
        row.definition_id,
        row.issue_id,
        row.status,
        row.trigger,
        row.started_at.isoformat(),
        row.finished_at.isoformat() if row.finished_at else None,
    )


def _step_json(row: StepRunRow) -> _StepResponse:
    return _StepResponse(
        row.id,
        row.step_id,
        row.visit,
        row.branch_token,
        row.status,
        row.attempt,
        row.input_json,
        row.output_json,
        row.error_code,
    )


def _transition_json(row: TransitionRow) -> _TransitionResponse:
    return _TransitionResponse(
        row.from_step_run_id, row.to_step_id, row.kind, row.reason, row.at.isoformat()
    )


def _human_json(
    row: HumanTaskRow, steps: list[StepRunRow], workflow: CompiledWorkflow
) -> _HumanResponse:
    step_run: StepRunRow | None = next(
        (visit for visit in steps if visit.id == row.step_run_id), None
    )
    step: StepSpec | None = workflow.step(step_run.step_id) if step_run is not None else None
    outcomes: tuple[str, ...] = ()
    if step is not None and isinstance(step, HumanTaskStep):
        outcomes = tuple(route.name for route in step.outcomes)
    elif step is not None and isinstance(step, EvidenceUploadStep):
        outcomes = ("accepted", "completed")
    return _HumanResponse(
        row.id,
        row.step_run_id,
        row.kind,
        row.title,
        row.assignee_ref,
        row.status,
        row.outcome,
        outcomes,
    )
