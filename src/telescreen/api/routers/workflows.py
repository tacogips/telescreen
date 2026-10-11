"""Workflow definition and run-start APIs."""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.context import ApiContext
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import RunTrigger, SourceFormat
from telescreen.db.models import WorkflowDefinitionRow
from telescreen.workflow.dsl.loader import WorkflowValidationError
from telescreen.workflow.dsl.schema import workflow_schema_text
from telescreen.workflow.dsl.validate import ValidationIssue
from telescreen.workflow.engine.definitions import WorkflowService
from telescreen.workflow.engine.engine import RunEngine


@dataclass(frozen=True)
class WorkflowsRouterDeps:
    """Dependencies required by workflow routes."""

    ctx: ApiContext
    service: WorkflowService
    engine: RunEngine


@pydantic.dataclasses.dataclass(frozen=True)
class WorkflowSourceBody:
    """Workflow source upload and validation body."""

    source_format: SourceFormat
    source_text: str


@pydantic.dataclasses.dataclass(frozen=True)
class ActivateBody:
    """Version selected for activation."""

    version: int


@pydantic.dataclasses.dataclass(frozen=True)
class StartRunBody:
    """Optional issue association for a manually started workflow."""

    issue_id: str | None = None


@dataclass(frozen=True)
class _DefinitionResponse:
    id: str
    name: str
    version: int
    active: bool


@dataclass(frozen=True)
class _VersionResponse:
    id: str
    name: str
    version: int
    active: bool
    source_format: str
    source_text: str
    compiled_json: str


def build_workflows_router(deps: WorkflowsRouterDeps) -> APIRouter:
    """Build authenticated workflow definition and start endpoints."""
    router: APIRouter = APIRouter(tags=["workflows"])

    @router.get("/api/workflows")
    async def list_workflows(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.WORKFLOWS_READ))
        ],
    ) -> JSONResponse:
        del principal
        async with deps.ctx.sessions() as session:
            rows: tuple[WorkflowDefinitionRow, ...] = tuple(await deps.service.list(session))
        return JSONResponse(content={"items": [asdict(_definition_json(row)) for row in rows]})

    @router.get("/api/workflows/schema")
    async def schema(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.WORKFLOWS_READ))
        ],
    ) -> Response:
        del principal
        return Response(content=workflow_schema_text(), media_type="application/schema+json")

    @router.get("/api/workflows/{name}/versions/{version}")
    async def get_version(
        name: str,
        version: int,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.WORKFLOWS_READ))
        ],
    ) -> JSONResponse:
        del principal
        async with deps.ctx.sessions() as session:
            row: WorkflowDefinitionRow | None = await deps.service.get(session, name, version)
        if row is None:
            from telescreen.core.errors import TelescreenError
            from telescreen.core.vocabulary import ErrorCode

            raise TelescreenError(ErrorCode.NOT_FOUND, "workflow version not found")
        return JSONResponse(content=asdict(_version_json(row)))

    @router.get("/api/workflows/{name}")
    async def get_workflow(
        name: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.WORKFLOWS_READ))
        ],
    ) -> JSONResponse:
        del principal
        async with deps.ctx.sessions() as session:
            rows: tuple[WorkflowDefinitionRow, ...] = tuple(
                await deps.service.versions(session, name)
            )
        if not rows:
            from telescreen.core.errors import TelescreenError
            from telescreen.core.vocabulary import ErrorCode

            raise TelescreenError(ErrorCode.NOT_FOUND, "workflow not found")
        return JSONResponse(content={"items": [asdict(_definition_json(row)) for row in rows]})

    @router.post("/api/workflows/validate")
    async def validate(
        body: WorkflowSourceBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.WORKFLOWS_WRITE))
        ],
    ) -> JSONResponse:
        del principal
        errors: Sequence[ValidationIssue] = await deps.service.validate(
            body.source_format, body.source_text
        )
        return JSONResponse(
            content={
                "valid": not errors,
                "errors": [{"path": e.path, "code": e.code} for e in errors],
            }
        )

    @router.post("/api/workflows", status_code=201)
    async def upload(
        body: WorkflowSourceBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.WORKFLOWS_WRITE))
        ],
    ) -> JSONResponse:
        row: WorkflowDefinitionRow
        try:
            async with deps.ctx.sessions.begin() as session:
                row = await deps.service.upload(
                    session, body.source_format, body.source_text, principal.actor_ref()
                )
        except WorkflowValidationError as error:
            return JSONResponse(
                status_code=422,
                content={
                    "valid": False,
                    "errors": [{"path": e.path, "code": e.code} for e in error.issues],
                },
            )
        return JSONResponse(status_code=201, content=asdict(_definition_json(row)))

    @router.post("/api/workflows/{name}/activate")
    async def activate(
        name: str,
        body: ActivateBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.WORKFLOWS_ACTIVATE))
        ],
    ) -> JSONResponse:
        async with deps.ctx.sessions.begin() as session:
            row: WorkflowDefinitionRow = await deps.service.activate(session, name, body.version)
        return JSONResponse(content=asdict(_definition_json(row)))

    @router.post("/api/workflows/{name}/runs", status_code=201)
    async def start_run(
        name: str,
        body: StartRunBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.RUNS_WRITE))
        ],
    ) -> JSONResponse:
        async with deps.ctx.sessions.begin() as session:
            row: WorkflowDefinitionRow | None = await deps.service.get(session, name)
            if row is None:
                from telescreen.core.errors import TelescreenError
                from telescreen.core.vocabulary import ErrorCode

                raise TelescreenError(ErrorCode.NOT_FOUND, "workflow not found")
            run_id: str = await deps.engine.start_run(
                session, row.id, body.issue_id, RunTrigger.MANUAL
            )
        return JSONResponse(status_code=201, content={"run_id": run_id})

    return router


def _definition_json(row: WorkflowDefinitionRow) -> _DefinitionResponse:
    return _DefinitionResponse(row.id, row.name, row.version, row.active)


def _version_json(row: WorkflowDefinitionRow) -> _VersionResponse:
    return _VersionResponse(
        row.id,
        row.name,
        row.version,
        row.active,
        row.source_format,
        row.source_text,
        row.compiled_json,
    )
