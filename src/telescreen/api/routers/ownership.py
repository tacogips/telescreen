"""Ownership rule endpoints kept unregistered until router assembly."""

from dataclasses import dataclass
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.context import ApiContext
from telescreen.core.json import (
    JsonValue,
    dump_json,
    json_array,
    json_bool,
    json_int,
    json_null,
    json_object,
    json_str,
)
from telescreen.core.permissions import Permission
from telescreen.db.models import OwnershipRuleRow, TeamRow
from telescreen.issues.ownership import OwnershipRuleService, RuleMatch


@pydantic.dataclasses.dataclass(frozen=True)
class OwnershipRuleBody:
    """Validated ownership rule definition."""

    pattern: str
    team: str
    source: str | None = None
    enabled: bool = True


@pydantic.dataclasses.dataclass(frozen=True)
class OwnershipRuleTestBody:
    """Transient locator test request."""

    source: str
    locator: str


@pydantic.dataclasses.dataclass(frozen=True)
class OwnershipReorderBody:
    """Requested complete ownership rule order."""

    ids: list[str]


@dataclass(frozen=True)
class OwnershipRouterDeps:
    """Services required by ownership rule routes."""

    ctx: ApiContext
    rules: OwnershipRuleService


async def _rule_json(session: AsyncSession, rule: OwnershipRuleRow) -> JsonValue:
    """Build a safe ownership rule response with the public team slug."""
    team: TeamRow | None = await session.get(TeamRow, rule.team_id)
    return json_object(
        (
            ("id", json_str(rule.id)),
            ("position", json_int(rule.position)),
            ("source", json_str(rule.source) if rule.source is not None else json_null()),
            ("pattern", json_str(rule.pattern)),
            ("team", json_str(team.slug) if team is not None else json_null()),
            ("enabled", json_bool(rule.enabled)),
        )
    )


def build_ownership_router(deps: OwnershipRouterDeps) -> APIRouter:
    """Build ownership rule CRUD and transient matching test routes."""
    router: APIRouter = APIRouter()

    @router.get("/api/ownership-rules")
    async def list_rules(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.OWNERSHIP_READ))
        ],
    ) -> Response:
        """List configured ownership rules in order."""
        del principal
        async with deps.ctx.sessions() as session:
            rows: tuple[OwnershipRuleRow, ...] = await deps.rules.list(session)
            items: list[JsonValue] = []
            for row in rows:
                items.append(await _rule_json(session, row))
        return Response(
            content=dump_json(
                json_object(
                    (
                        ("items", json_array(tuple(items))),
                        ("next_cursor", json_null()),
                    )
                )
            ),
            media_type="application/json",
        )

    @router.post("/api/ownership-rules/test")
    async def test_rule(
        body: OwnershipRuleTestBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.OWNERSHIP_READ))
        ],
    ) -> JSONResponse:
        """Test a transient display locator without returning it."""
        del principal
        async with deps.ctx.sessions() as session:
            match: RuleMatch | None = await deps.rules.test(session, body.source, body.locator)
        return JSONResponse(
            content={
                "rule_id": match.rule_id if match is not None else None,
                "team": match.team_slug if match is not None else None,
            }
        )

    @router.post("/api/ownership-rules", status_code=201)
    async def create_rule(
        body: OwnershipRuleBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.OWNERSHIP_WRITE))
        ],
    ) -> Response:
        """Create a checked ownership rule."""
        async with deps.ctx.sessions.begin() as session:
            row: OwnershipRuleRow = await deps.rules.create(
                session, body.source, body.pattern, body.team, body.enabled, principal.actor_ref()
            )
            content: JsonValue = await _rule_json(session, row)
        return Response(status_code=201, content=dump_json(content), media_type="application/json")

    @router.put("/api/ownership-rules/{rule_id}")
    async def update_rule(
        rule_id: str,
        body: OwnershipRuleBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.OWNERSHIP_WRITE))
        ],
    ) -> Response:
        """Update a checked ownership rule."""
        async with deps.ctx.sessions.begin() as session:
            row: OwnershipRuleRow = await deps.rules.update(
                session,
                rule_id,
                body.source,
                body.pattern,
                body.team,
                body.enabled,
                principal.actor_ref(),
            )
            content: JsonValue = await _rule_json(session, row)
        return Response(content=dump_json(content), media_type="application/json")

    @router.delete("/api/ownership-rules/{rule_id}", status_code=204)
    async def delete_rule(
        rule_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.OWNERSHIP_WRITE))
        ],
    ) -> Response:
        """Delete an ownership rule."""
        async with deps.ctx.sessions.begin() as session:
            await deps.rules.delete(session, rule_id, principal.actor_ref())
        return Response(status_code=204)

    @router.post("/api/ownership-rules/reorder", status_code=204)
    async def reorder_rules(
        body: OwnershipReorderBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.OWNERSHIP_WRITE))
        ],
    ) -> Response:
        """Replace ownership rule order, requiring every id exactly once."""
        async with deps.ctx.sessions.begin() as session:
            await deps.rules.reorder(session, tuple(body.ids), principal.actor_ref())
        return Response(status_code=204)

    return router
