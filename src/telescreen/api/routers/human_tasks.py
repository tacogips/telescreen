"""Human task assignment and completion APIs."""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import Select, select

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.context import ApiContext
from telescreen.core.errors import TelescreenError
from telescreen.core.json import json_str, parse_json
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import (
    ActorKind,
    ErrorCode,
    ExceptionDuration,
    HumanTaskKind,
    RemediationAction,
)
from telescreen.db.models import (
    HumanTaskRow,
    StepRunRow,
    WorkflowDefinitionRow,
    WorkflowRunRow,
)
from telescreen.masking.redact import Redactor
from telescreen.ports.directory import OwnerDirectory, RecipientResolver
from telescreen.ports.evidence import EvidenceQuery
from telescreen.ports.issues import IssueRemediation
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
    owners: OwnerDirectory | None = None
    remediation: IssueRemediation | None = None


@pydantic.dataclasses.dataclass(frozen=True)
class CompleteHumanTaskBody:
    """Outcome and optional comment submitted by an assignee."""

    outcome: str
    comment: str | None = None
    exception_duration: ExceptionDuration | None = None


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
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.HUMAN_TASKS_READ))
        ],
        assignee: str | None = None,
        status: str | None = None,
    ) -> JSONResponse:
        wants_me: bool = assignee == "me" or assignee is None
        if principal.member_id is None and wants_me:
            raise TelescreenError(ErrorCode.FORBIDDEN, "task assignment scope is required")
        if principal.member_id is not None and wants_me and deps.owners is None:
            raise HTTPException(status_code=503, detail="owner directory unavailable")
        if assignee == "all" and not principal.allows(Permission.HUMAN_TASKS_MANAGE_ANY):
            raise TelescreenError(ErrorCode.FORBIDDEN, "administrator permission is required")
        async with deps.ctx.sessions() as session:
            query: Select[tuple[HumanTaskRow]] = select(HumanTaskRow)
            if wants_me and principal.member_id is not None and deps.owners is not None:
                member_ref: str | None = await deps.owners.member_ref(session, principal.member_id)
                team_refs: tuple[str, ...] = await deps.owners.team_refs_of(
                    session, principal.member_id
                )
                assignee_refs: tuple[str, ...] = (
                    (member_ref,) if member_ref is not None else ()
                ) + team_refs
                if assignee_refs:
                    query = query.where(HumanTaskRow.assignee_ref.in_(assignee_refs))
                else:
                    return JSONResponse(content={"items": []})
            elif assignee is not None and assignee != "all":
                query = query.where(HumanTaskRow.assignee_ref == assignee)
            if status is not None:
                query = query.where(HumanTaskRow.status == status)
            rows: list[HumanTaskRow] = list((await session.scalars(query)).all())
        return JSONResponse(content={"items": [asdict(_task_json(row)) for row in rows]})

    @router.post("/{task_id}/complete")
    async def complete(
        task_id: str,
        body: CompleteHumanTaskBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.HUMAN_TASKS_COMPLETE))
        ],
    ) -> JSONResponse:
        async with deps.ctx.sessions.begin() as session:
            task: HumanTaskRow | None = await session.get(HumanTaskRow, task_id)
            if task is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "human task not found")
            if task.status != "open":
                raise TelescreenError(ErrorCode.CONFLICT, "human task is not open")
            if not principal.allows(Permission.HUMAN_TASKS_MANAGE_ANY) and (
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
            if task.kind == HumanTaskKind.OWNER_REVIEW.value:
                if deps.remediation is None:
                    raise HTTPException(status_code=503, detail="issue remediation unavailable")
                if run.issue_id is None:
                    raise TelescreenError(ErrorCode.CONFLICT, "owner review has no issue")
                if body.outcome == "exception":
                    if (
                        body.comment is None
                        or not body.comment.strip()
                        or safe_comment is None
                        or not safe_comment.strip()
                        or body.exception_duration is None
                        or body.exception_duration is ExceptionDuration.FOREVER
                    ):
                        raise TelescreenError(
                            ErrorCode.VALIDATION_ERROR,
                            "exception comment and duration are required",
                        )
                    await deps.remediation.request_exception(
                        session,
                        run.issue_id,
                        safe_comment,
                        body.exception_duration,
                        principal.actor_ref(),
                        principal.allows(Permission.HUMAN_TASKS_MANAGE_ANY),
                    )
                elif body.outcome in ("remove", "restrict"):
                    await deps.remediation.request_verification(
                        session,
                        run.issue_id,
                        RemediationAction(body.outcome),
                        False,
                        principal.actor_ref(),
                        safe_comment,
                    )
                elif body.outcome == "not_mine":
                    await deps.remediation.release_to_team(
                        session, run.issue_id, principal.actor_ref()
                    )
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
