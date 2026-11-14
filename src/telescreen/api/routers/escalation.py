"""Escalation policy management endpoints."""

from dataclasses import asdict, dataclass
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from telescreen.api.common.auth import Principal, require_scope
from telescreen.api.common.context import ApiContext
from telescreen.core.vocabulary import ApiScope, SourceFormat
from telescreen.db.models import EscalationPolicyRow
from telescreen.workflow.escalation.service import EscalationPolicyService


@dataclass(frozen=True)
class EscalationRouterDeps:
    """Dependencies for escalation policy routes."""

    ctx: ApiContext
    service: EscalationPolicyService


@pydantic.dataclasses.dataclass(frozen=True)
class EscalationPolicyBody:
    """Source document for an escalation policy."""

    tiers_json: str
    source_format: SourceFormat


@dataclass(frozen=True)
class _PolicyResponse:
    id: str
    name: str
    tiers_json: str


def build_escalation_router(deps: EscalationRouterDeps) -> APIRouter:
    """Build read and admin escalation policy endpoints."""
    router: APIRouter = APIRouter(prefix="/api/escalation-policies", tags=["escalation"])

    @router.get("")
    async def list_policies(
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.READ))],
    ) -> JSONResponse:
        del principal
        async with deps.ctx.sessions() as session:
            rows: tuple[EscalationPolicyRow, ...] = tuple(await deps.service.list(session))
        return JSONResponse(content={"items": [asdict(_policy_json(row)) for row in rows]})

    @router.post("", status_code=201)
    async def create_policy(
        body: EscalationPolicyBody,
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.ADMIN))],
    ) -> JSONResponse:
        async with deps.ctx.sessions.begin() as session:
            row: EscalationPolicyRow = await deps.service.create(
                session, body.tiers_json, body.source_format, principal.actor_ref()
            )
        return JSONResponse(status_code=201, content=asdict(_policy_json(row)))

    @router.put("/{name}")
    async def update_policy(
        name: str,
        body: EscalationPolicyBody,
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.ADMIN))],
    ) -> JSONResponse:
        async with deps.ctx.sessions.begin() as session:
            row: EscalationPolicyRow = await deps.service.update(
                session, name, body.tiers_json, body.source_format, principal.actor_ref()
            )
        return JSONResponse(content=asdict(_policy_json(row)))

    return router


def _policy_json(row: EscalationPolicyRow) -> _PolicyResponse:
    return _PolicyResponse(row.id, row.name, row.tiers_json)
