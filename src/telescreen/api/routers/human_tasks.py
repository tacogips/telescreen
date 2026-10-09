"""Human task assignment and completion APIs."""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import Select, select

from telescreen.api.common.auth import Principal, require_scope
from telescreen.api.common.context import ApiContext
from telescreen.core.errors import TelescreenError
from telescreen.core.json import json_str, parse_json
from telescreen.core.vocabulary import ActorKind, ApiScope, ErrorCode
from telescreen.db.models import (
    HumanTaskRow,
    StepRunRow,
    WorkflowDefinitionRow,
    WorkflowRunRow,
)
from telescreen.masking.redact import Redactor
from telescreen.ports.directory import RecipientResolver
from telescreen.ports.evidence import EvidenceQuery
from telescreen.ports.workflow import RunSignaler, StepSignal, StepSignalKind
from telescreen.workflow.dsl.loader import CompiledWorkflow, compiled_from_json
from telescreen.workflow.dsl.model import EvidenceUploadStep, HumanTaskStep, StepSpec
from telescreen.workflow.engine.repository import WorkflowRepository


@dataclass(frozen=True)
class HumanTasksRouterDeps:
    """Dependencies needed by human task routes."""

    ctx: ApiContext
    resolver: RecipientResolver
    evidence: EvidenceQuery
    signaler: RunSignaler
    repository: WorkflowRepository
    redactor: Redactor


@pydantic.dataclasses.dataclass(frozen=True)
class CompleteHumanTaskBody:
    """Outcome and optional comment submitted by an assignee."""

    outcome: str
    comment: str | None = None


@dataclass(frozen=True)
class _HumanTaskResponse:
    id: str
    run_id: str
    step_run_id: str
    kind: str
    title: str
    assignee_ref: str
    due_at: str | None
    status: str
    outcome: str | None


def build_human_tasks_router(deps: HumanTasksRouterDeps) -> APIRouter:
    """Build task listing and completion endpoints."""
    router: APIRouter = APIRouter(prefix="/api/human-tasks", tags=["human-tasks"])

    @router.get("")
    async def list_tasks(
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.READ))],
        assignee: str | None = None,
        status: str | None = None,
    ) -> JSONResponse:
        selected: str = (
            (f"member:{principal.member_id}" if assignee == "me" or assignee is None else assignee)
            if principal.member_id
            else (assignee or "")
        )
        if principal.member_id is None and not principal.has(ApiScope.ADMIN) and not selected:
            raise TelescreenError(ErrorCode.FORBIDDEN, "task assignment scope is required")
        async with deps.ctx.sessions() as session:
            query: Select[tuple[HumanTaskRow]] = select(HumanTaskRow)
            if selected and selected != "me":
                query = query.where(HumanTaskRow.assignee_ref == selected)
            if status is not None:
                query = query.where(HumanTaskRow.status == status)
            rows: list[HumanTaskRow] = list((await session.scalars(query)).all())
        return JSONResponse(content={"items": [asdict(_task_json(row)) for row in rows]})

    @router.post("/{task_id}/complete")
    async def complete(
        task_id: str,
        body: CompleteHumanTaskBody,
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.WRITE))],
    ) -> JSONResponse:
        async with deps.ctx.sessions.begin() as session:
            task: HumanTaskRow | None = await session.get(HumanTaskRow, task_id)
            if task is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "human task not found")
            if task.status != "open":
                raise TelescreenError(ErrorCode.CONFLICT, "human task is not open")
            if not principal.has(ApiScope.ADMIN) and (
                principal.member_id is None
                or not await deps.resolver.is_member_of(
                    session, principal.member_id, task.assignee_ref
                )
            ):
                raise TelescreenError(ErrorCode.FORBIDDEN, "task is assigned to another recipient")
            step_run: StepRunRow | None = await deps.repository.step_run(session, task.step_run_id)
            run: WorkflowRunRow | None = await deps.repository.run(session, task.run_id)
            if step_run is None or run is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "workflow task context not found")
            definition: WorkflowDefinitionRow | None = await deps.repository.definition(
                session, run.definition_id
            )
            if definition is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "workflow definition not found")
            workflow: CompiledWorkflow = compiled_from_json(parse_json(definition.compiled_json))
            step: StepSpec = workflow.step(step_run.step_id)
            if isinstance(step, HumanTaskStep):
                outcomes: tuple[str, ...] = tuple(item.name for item in step.outcomes)
                min_files: int = 0
            elif isinstance(step, EvidenceUploadStep):
                outcomes = ("accepted", "completed")
                min_files = step.min_files
            else:
                raise TelescreenError(ErrorCode.WORKFLOW_INVALID, "human task step is invalid")
            if body.outcome not in outcomes:
                raise TelescreenError(ErrorCode.VALIDATION_ERROR, "outcome is not declared")
            if task.kind == "evidence" and (
                run.issue_id is None
                or await deps.evidence.accepted_count(session, run.issue_id, step_run.id)
                < min_files
            ):
                raise TelescreenError(ErrorCode.EVIDENCE_REQUIRED, "accepted evidence is required")
            safe_comment: str | None = None
            if body.comment is not None:
                safe_comment = deps.redactor.redact(body.comment).text
            now: datetime = deps.ctx.clock.now()
            task.status = "completed"
            task.outcome = body.outcome
            task.comment = safe_comment
            task.completed_by = principal.actor_ref()
            task.completed_at = now
            await deps.signaler.signal(
                session,
                step_run.id,
                StepSignal(
                    StepSignalKind.HUMAN_COMPLETED, body.outcome, json_str(safe_comment or "")
                ),
            )
            from telescreen.ports.audit import AuditEntry

            await deps.ctx.audit.append(
                session,
                AuditEntry(
                    ActorKind.MEMBER,
                    principal.actor_ref(),
                    "human_task.completed",
                    "human_task",
                    task.id,
                    "Human task completed",
                    json_str(body.outcome),
                ),
            )
        return JSONResponse(content={"id": task_id, "status": "completed"})

    return router


def _task_json(row: HumanTaskRow) -> _HumanTaskResponse:
    return _HumanTaskResponse(
        row.id,
        row.run_id,
        row.step_run_id,
        row.kind,
        row.title,
        row.assignee_ref,
        row.due_at.isoformat() if row.due_at else None,
        row.status,
        row.outcome,
    )
