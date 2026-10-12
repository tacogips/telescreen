"""Saved issue view API routes."""

from dataclasses import dataclass
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends
from fastapi.responses import Response

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.context import ApiContext
from telescreen.api.routers.issues_common import _optional_string, _response
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, json_array, json_bool, json_int, json_object, json_str
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import ErrorCode, SavedViewVisibility
from telescreen.db.models import SavedViewRow
from telescreen.issues.views import SavedViewInput, SavedViewService, SavedViewSummary


@pydantic.dataclasses.dataclass(frozen=True)
class SavedViewBody:
    """Create or replace a saved view."""

    name: str
    query: str
    sort: str
    period: str | None
    visibility: SavedViewVisibility


@pydantic.dataclasses.dataclass(frozen=True)
class DefaultViewBody:
    """Set or clear a caller's default saved view."""

    view_id: str | None


@dataclass(frozen=True)
class IssueViewsRouterDeps:
    """Dependencies required by saved view routes."""

    ctx: ApiContext
    service: SavedViewService


def build_issue_views_router(deps: IssueViewsRouterDeps) -> APIRouter:
    """Build saved-view routes with one permission dependency per operation."""
    router: APIRouter = APIRouter()

    @router.get("/api/views")
    async def list_views(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.VIEWS_READ))
        ],
    ) -> Response:
        """List caller-owned personal and shared views."""
        member_id: str = _member_id(principal)
        async with deps.ctx.sessions() as session:
            views: tuple[SavedViewSummary, ...] = await deps.service.list(session, member_id)
        return _response(
            json_object((("items", json_array(tuple(_summary(view) for view in views))),))
        )

    @router.post("/api/views")
    async def create_view(
        body: SavedViewBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.VIEWS_WRITE))
        ],
    ) -> Response:
        """Create a personal or shared saved view."""
        member_id: str = _member_id(principal)
        candidate: SavedViewInput = _candidate(body)
        row: SavedViewRow | None = None
        failure: TelescreenError | None = None
        async with deps.ctx.sessions.begin() as session:
            try:
                row = await deps.service.create(
                    session,
                    member_id,
                    candidate,
                    principal.actor_ref(),
                    principal.allows(Permission.VIEWS_MANAGE_SHARED),
                )
            except TelescreenError as error:
                failure = error
        if failure is not None:
            return _service_error(failure)
        if row is None:
            raise TelescreenError(ErrorCode.INTERNAL_ERROR, "internal error")
        return _response(_view_json(row, False, False), status_code=201)

    @router.patch("/api/views/{view_id}")
    async def update_view(
        view_id: str,
        body: SavedViewBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.VIEWS_WRITE))
        ],
    ) -> Response:
        """Update a saved view under its ownership rules."""
        member_id: str = _member_id(principal)
        row: SavedViewRow | None = None
        failure: TelescreenError | None = None
        async with deps.ctx.sessions.begin() as session:
            try:
                row = await deps.service.update(
                    session,
                    view_id,
                    member_id,
                    _candidate(body),
                    principal.actor_ref(),
                    principal.allows(Permission.VIEWS_MANAGE_SHARED),
                )
            except TelescreenError as error:
                failure = error
        if failure is not None:
            return _service_error(failure)
        if row is None:
            raise TelescreenError(ErrorCode.INTERNAL_ERROR, "internal error")
        return _response(_view_json(row, False, False))

    @router.delete("/api/views/{view_id}")
    async def delete_view(
        view_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.VIEWS_WRITE))
        ],
    ) -> Response:
        """Delete a saved view the caller is allowed to manage."""
        member_id: str = _member_id(principal)
        async with deps.ctx.sessions.begin() as session:
            await deps.service.delete(
                session,
                view_id,
                member_id,
                principal.actor_ref(),
                principal.allows(Permission.VIEWS_MANAGE_SHARED),
            )
        return _response(json_object((("deleted", json_bool(True)),)))

    @router.put("/api/views/{view_id}/star")
    async def star_view(
        view_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.VIEWS_WRITE))
        ],
    ) -> Response:
        """Star a visible view for the caller."""
        member_id: str = _member_id(principal)
        async with deps.ctx.sessions.begin() as session:
            await deps.service.star(session, view_id, member_id)
        return _response(json_object((("starred", json_bool(True)),)))

    @router.delete("/api/views/{view_id}/star")
    async def unstar_view(
        view_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.VIEWS_WRITE))
        ],
    ) -> Response:
        """Remove the caller's star from a saved view."""
        member_id: str = _member_id(principal)
        async with deps.ctx.sessions.begin() as session:
            await deps.service.unstar(session, view_id, member_id)
        return _response(json_object((("starred", json_bool(False)),)))

    @router.put("/api/views/default")
    async def set_default(
        body: DefaultViewBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.VIEWS_WRITE))
        ],
    ) -> Response:
        """Set or clear the caller's default view."""
        member_id: str = _member_id(principal)
        async with deps.ctx.sessions.begin() as session:
            await deps.service.set_default(session, member_id, body.view_id)
        return _response(json_object((("view_id", _optional_string(body.view_id)),)))

    return router


def _member_id(principal: Principal) -> str:
    """Require a user-bound principal for personal view state."""
    if principal.member_id is None:
        raise TelescreenError(ErrorCode.FORBIDDEN, "member identity required")
    return principal.member_id


def _candidate(body: SavedViewBody) -> SavedViewInput:
    """Convert an API body into the saved-view service DTO."""
    return SavedViewInput(body.name, body.query, body.sort, body.period, body.visibility)


def _summary(summary: SavedViewSummary) -> JsonValue:
    """Serialize a view with caller-specific star/default flags."""
    return _view_json(summary.row, summary.starred, summary.is_default)


def _view_json(row: SavedViewRow, starred: bool, is_default: bool) -> JsonValue:
    """Serialize persisted view values and caller flags."""
    return json_object(
        (
            ("id", json_str(row.id)),
            ("name", json_str(row.name)),
            ("query", json_str(row.query)),
            ("sort", json_str(row.sort)),
            ("period", _optional_string(row.period)),
            ("visibility", json_str(row.visibility)),
            ("starred", json_bool(starred)),
            ("is_default", json_bool(is_default)),
            ("position", json_int(row.position)),
        )
    )


def _service_error(error: TelescreenError) -> Response:
    """Render safe saved-view errors with contract-specific HTTP statuses."""
    if error.code in (
        ErrorCode.RAW_VALUE_DETECTED,
        ErrorCode.INVALID_QUERY,
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.LIMIT_EXCEEDED,
    ):
        status_code: int = 422
    elif error.code is ErrorCode.FORBIDDEN:
        status_code = 403
    elif error.code is ErrorCode.NOT_FOUND:
        status_code = 404
    else:
        status_code = 500
    return _response(
        json_object(
            (
                (
                    "error",
                    json_object(
                        (("code", json_str(error.code.value)), ("detail", json_str(error.detail)))
                    ),
                ),
            )
        ),
        status_code=status_code,
    )
