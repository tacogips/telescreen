"""Placement policy validation and version lifecycle endpoints."""

from dataclasses import dataclass
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends
from fastapi.responses import Response

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.context import ApiContext
from telescreen.core.clock import ensure_utc
from telescreen.core.errors import TelescreenError
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
from telescreen.core.vocabulary import ErrorCode, SourceFormat
from telescreen.issues.policy.loader import PolicyValidationError
from telescreen.issues.policy.model import PolicyVersionView
from telescreen.issues.policy.store import PolicyStore
from telescreen.workflow.dsl.validate import ValidationIssue


@pydantic.dataclasses.dataclass(frozen=True)
class PolicySourceBody:
    """Validated policy source and format."""

    source_format: SourceFormat
    source_text: str


@pydantic.dataclasses.dataclass(frozen=True)
class PolicyActivateBody:
    """Validated policy activation request."""

    version: int


@dataclass(frozen=True)
class PoliciesRouterDeps:
    """Dependencies required by placement policy endpoints."""

    ctx: ApiContext
    store: PolicyStore | None = None


def build_policies_router(deps: PoliciesRouterDeps) -> APIRouter:
    """Build authenticated policy read and admin mutation routes."""
    router: APIRouter = APIRouter()

    @router.get("/api/policies")
    async def list_policies(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.POLICIES_READ))
        ],
    ) -> Response:
        """List one active or latest version for each policy name."""
        del principal
        if deps.store is None:
            return _unavailable()
        store: PolicyStore = deps.store
        async with deps.ctx.sessions() as session:
            views: tuple[PolicyVersionView, ...] = await store.list(session)
        return _response(
            json_object((("items", json_array(tuple(_view_json(view) for view in views))),))
        )

    @router.get("/api/policies/{name}/versions/{version}")
    async def get_policy_version(
        name: str,
        version: int,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.POLICIES_READ))
        ],
    ) -> Response:
        """Return a stored policy version including its redacted source."""
        del principal
        if deps.store is None:
            return _unavailable()
        store: PolicyStore = deps.store
        async with deps.ctx.sessions() as session:
            view: PolicyVersionView = await store.version(session, name, version)
        return _response(_view_json(view, include_source=True))

    @router.get("/api/policies/{name}")
    async def get_policy(
        name: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.POLICIES_READ))
        ],
    ) -> Response:
        """Return every stored version for a policy name."""
        del principal
        if deps.store is None:
            return _unavailable()
        store: PolicyStore = deps.store
        async with deps.ctx.sessions() as session:
            views: tuple[PolicyVersionView, ...] = await store.versions(session, name)
        if not views:
            raise TelescreenError(ErrorCode.NOT_FOUND, "policy not found")
        return _response(
            json_object((("items", json_array(tuple(_view_json(view) for view in views))),))
        )

    @router.post("/api/policies/validate")
    async def validate_policy(
        body: PolicySourceBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.POLICIES_READ))
        ],
    ) -> Response:
        """Validate source and return only path/code diagnostics."""
        del principal
        if deps.store is None:
            return _unavailable()
        store: PolicyStore = deps.store
        issues: tuple[ValidationIssue, ...] = store.validate(body.source_text, body.source_format)
        return _response(_validation_json(issues))

    @router.post("/api/policies", status_code=201)
    async def create_policy(
        body: PolicySourceBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.POLICIES_WRITE))
        ],
    ) -> Response:
        """Create an inactive version without echoing invalid source."""
        if deps.store is None:
            return _unavailable()
        store: PolicyStore = deps.store
        try:
            async with deps.ctx.sessions.begin() as session:
                view: PolicyVersionView = await store.create(
                    session,
                    body.source_text,
                    body.source_format,
                    principal.actor_ref(),
                )
        except PolicyValidationError as error:
            return _response(_validation_json(error.issues), status_code=422)
        return _response(_view_json(view), status_code=201)

    @router.post("/api/policies/{name}/activate")
    async def activate_policy(
        name: str,
        body: PolicyActivateBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.POLICIES_WRITE))
        ],
    ) -> Response:
        """Activate a version and enqueue issue re-evaluation."""
        if deps.store is None:
            return _unavailable()
        store: PolicyStore = deps.store
        async with deps.ctx.sessions.begin() as session:
            view: PolicyVersionView = await store.activate(
                session, name, body.version, principal.actor_ref()
            )
        return _response(_view_json(view))

    @router.post("/api/policies/{name}/disable")
    async def disable_policy(
        name: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.POLICIES_WRITE))
        ],
    ) -> Response:
        """Disable a policy and enqueue issue re-evaluation."""
        if deps.store is None:
            return _unavailable()
        store: PolicyStore = deps.store
        async with deps.ctx.sessions.begin() as session:
            await store.disable(session, name, principal.actor_ref())
        return _response(json_object((("name", json_str(name)), ("enabled", json_bool(False)))))

    return router


def _view_json(view: PolicyVersionView, include_source: bool = False) -> JsonValue:
    """Serialize a safe policy version projection."""
    members: list[tuple[str, JsonValue]] = [
        ("name", json_str(view.name)),
        ("version", json_int(view.version)),
        ("effect", json_str(view.effect.value)),
        ("enabled", json_bool(view.enabled)),
        ("active", json_bool(view.active)),
        ("owner_team", _optional_string(view.owner_team)),
        ("source_format", json_str(view.source_format.value)),
        ("created_by", json_str(view.created_by)),
        ("created_at", json_str(ensure_utc(view.created_at).isoformat())),
    ]
    if include_source:
        members.append(("source_text", json_str(view.source_text)))
    return json_object(tuple(members))


def _validation_json(issues: tuple[ValidationIssue, ...]) -> JsonValue:
    """Build validation output containing only paths and stable error codes."""
    errors: list[JsonValue] = [
        json_object((("path", json_str(issue.path)), ("code", json_str(issue.code))))
        for issue in issues
    ]
    return json_object((("valid", json_bool(not issues)), ("errors", json_array(tuple(errors)))))


def _optional_string(value: str | None) -> JsonValue:
    """Serialize an optional string."""
    return json_str(value) if value is not None else json_null()


def _response(value: JsonValue, status_code: int = 200) -> Response:
    """Serialize the closed JSON model into an API response."""
    return Response(
        content=dump_json(value), status_code=status_code, media_type="application/json"
    )


def _unavailable() -> Response:
    """Return the static feature-off dependency response."""
    return _response(
        json_object(
            (
                (
                    "error",
                    json_object(
                        (
                            ("code", json_str("internal_error")),
                            ("detail", json_str("internal error")),
                        )
                    ),
                ),
            )
        ),
        status_code=503,
    )
