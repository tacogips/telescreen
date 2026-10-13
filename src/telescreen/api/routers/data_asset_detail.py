"""Detail, event, item, and member mutation routes for data assets."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends, Query, Response

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.context import ApiContext
from telescreen.api.routers.data_common import asset_detail_json, event_json, item_json
from telescreen.core.errors import TelescreenError
from telescreen.core.json import dump_json, json_array, json_int, json_null, json_object, json_str
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import ErrorCode
from telescreen.inventory.assets import AssetService
from telescreen.inventory.queries import (
    AssetDetailView,
    AssetQueries,
    EventPage,
    EventView,
    ItemPage,
    SeriesPoint,
)


@pydantic.dataclasses.dataclass(frozen=True)
class AssetPatchBody:
    """Safe member edits for a registered asset."""

    name: str | None = field(default=None, repr=False)
    kind: str | None = None


@pydantic.dataclasses.dataclass(frozen=True)
class ManualEventBody:
    """Count-only partial change annotation."""

    occurred_at: datetime
    items_added: int | None = None
    items_removed: int | None = None
    item_count_after: int | None = None
    note: str | None = field(default=None, repr=False)


@pydantic.dataclasses.dataclass(frozen=True)
class MarkDeletedBody:
    """Optional deletion occurrence time and safe note."""

    occurred_at: datetime | None = None
    note: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class DataAssetDetailRouterDeps:
    """Dependencies needed by asset detail routes."""

    ctx: ApiContext
    queries: AssetQueries
    assets: AssetService


def build_data_asset_detail_router(deps: DataAssetDetailRouterDeps) -> APIRouter:
    """Build the eight standalone asset detail and mutation routes."""
    router: APIRouter = APIRouter(tags=["data"])

    @router.get("/api/data/assets/{asset_id}")
    async def detail(
        asset_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DATA_READ))
        ],
    ) -> Response:
        del principal
        async with deps.ctx.sessions() as session:
            view: AssetDetailView | None = await deps.queries.detail(
                session, asset_id, deps.ctx.clock.now().date()
            )
        if view is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "data asset not found")
        return Response(dump_json(asset_detail_json(view)), media_type="application/json")

    @router.get("/api/data/assets/{asset_id}/events")
    async def events(
        asset_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DATA_READ))
        ],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: str | None = None,
    ) -> Response:
        del principal
        await _require_asset(deps, asset_id)
        async with deps.ctx.sessions() as session:
            page: EventPage = await deps.queries.events(session, asset_id, limit, cursor)
        return Response(
            dump_json(
                json_object(
                    (
                        ("items", json_array(tuple(event_json(event) for event in page.items))),
                        (
                            "next_cursor",
                            json_str(page.next_cursor) if page.next_cursor else json_null(),
                        ),
                    )
                )
            ),
            media_type="application/json",
        )

    @router.get("/api/data/assets/{asset_id}/items")
    async def items(
        asset_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DATA_READ))
        ],
        kind: str = "item",
        state: str = "present",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: str | None = None,
    ) -> Response:
        del principal
        if kind not in ("item", "value") or state not in ("present", "removed"):
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid item filter")
        await _require_asset(deps, asset_id)
        async with deps.ctx.sessions() as session:
            page: ItemPage = await deps.queries.items(session, asset_id, kind, state, limit, cursor)
        return Response(
            dump_json(
                json_object(
                    (
                        ("items", json_array(tuple(item_json(item) for item in page.items))),
                        (
                            "next_cursor",
                            json_str(page.next_cursor) if page.next_cursor else json_null(),
                        ),
                    )
                )
            ),
            media_type="application/json",
        )

    @router.get("/api/data/assets/{asset_id}/item-series")
    async def item_series(
        asset_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DATA_READ))
        ],
        days: Annotated[int, Query(ge=1, le=365)] = 90,
    ) -> Response:
        del principal
        await _require_asset(deps, asset_id)
        async with deps.ctx.sessions() as session:
            points: tuple[SeriesPoint, ...] = await deps.queries.item_series(
                session, asset_id, days, deps.ctx.clock.now().date()
            )
        return Response(
            dump_json(
                json_object(
                    (
                        (
                            "points",
                            json_array(
                                tuple(
                                    json_object(
                                        (
                                            ("at", json_str(point.at.isoformat())),
                                            ("item_count", json_int(point.item_count)),
                                        )
                                    )
                                    for point in points
                                )
                            ),
                        ),
                    )
                )
            ),
            media_type="application/json",
        )

    @router.patch("/api/data/assets/{asset_id}")
    async def patch_asset(
        asset_id: str,
        body: AssetPatchBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DATA_WRITE))
        ],
    ) -> Response:
        async with deps.ctx.sessions.begin() as session:
            await deps.assets.patch(session, asset_id, body.name, body.kind, principal.actor_ref())
            view: AssetDetailView | None = await deps.queries.detail(
                session, asset_id, deps.ctx.clock.now().date()
            )
        if view is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "data asset not found")
        return Response(dump_json(asset_detail_json(view)), media_type="application/json")

    @router.post("/api/data/assets/{asset_id}/events", status_code=201)
    async def record_event(
        asset_id: str,
        body: ManualEventBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DATA_WRITE))
        ],
    ) -> Response:
        async with deps.ctx.sessions.begin() as session:
            await deps.assets.record_event(
                session,
                asset_id,
                body.occurred_at,
                principal.actor_ref(),
                body.items_added,
                body.items_removed,
                body.item_count_after,
                body.note,
            )
            page: EventPage = await deps.queries.events(session, asset_id, 1, None)
        event: EventView | None = page.items[0] if page.items else None
        if event is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "data asset event not found")
        return Response(
            dump_json(event_json(event)), status_code=201, media_type="application/json"
        )

    @router.post("/api/data/assets/{asset_id}/mark-deleted")
    async def mark_deleted(
        asset_id: str,
        body: MarkDeletedBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DATA_WRITE))
        ],
    ) -> Response:
        async with deps.ctx.sessions.begin() as session:
            await deps.assets.mark_deleted(
                session, asset_id, principal.actor_ref(), body.occurred_at, body.note
            )
            view: AssetDetailView | None = await deps.queries.detail(
                session, asset_id, deps.ctx.clock.now().date()
            )
        if view is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "data asset not found")
        return Response(dump_json(asset_detail_json(view)), media_type="application/json")

    @router.post("/api/data/assets/{asset_id}/restore")
    async def restore(
        asset_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DATA_WRITE))
        ],
    ) -> Response:
        async with deps.ctx.sessions.begin() as session:
            await deps.assets.restore(session, asset_id, principal.actor_ref())
            view: AssetDetailView | None = await deps.queries.detail(
                session, asset_id, deps.ctx.clock.now().date()
            )
        if view is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "data asset not found")
        return Response(dump_json(asset_detail_json(view)), media_type="application/json")

    return router


async def _require_asset(deps: DataAssetDetailRouterDeps, asset_id: str) -> None:
    """Raise a stable not-found response for unknown asset ids."""
    async with deps.ctx.sessions() as session:
        row: AssetDetailView | None = await deps.queries.detail(
            session, asset_id, deps.ctx.clock.now().date()
        )
    if row is None:
        raise TelescreenError(ErrorCode.NOT_FOUND, "data asset not found")
