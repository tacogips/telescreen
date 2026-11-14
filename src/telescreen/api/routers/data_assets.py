"""Collection, count, export, and resource lookup routes for data assets."""

import re
from dataclasses import dataclass
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.context import ApiContext
from telescreen.api.routers.data_common import asset_json, parse_filters
from telescreen.core.errors import TelescreenError
from telescreen.core.json import (
    dump_json,
    json_array,
    json_bool,
    json_int,
    json_null,
    json_object,
    json_str,
)
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import ErrorCode
from telescreen.inventory.export import AssetExporter, CsvExport
from telescreen.inventory.queries import (
    AssetFilters,
    AssetPage,
    AssetQueries,
    AssetView,
    CountsView,
)
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.identity_context import actor_kind_for


@dataclass(frozen=True)
class DataAssetsRouterDeps:
    """Dependencies needed by data asset collection routes."""

    ctx: ApiContext
    queries: AssetQueries
    exporter: AssetExporter
    audit: AuditLog
    redactor: Redactor
    inventory_sessions: async_sessionmaker[AsyncSession] | None = None


def build_data_assets_router(deps: DataAssetsRouterDeps) -> APIRouter:
    """Build the standalone data asset collection router."""
    data_sessions: async_sessionmaker[AsyncSession] = deps.inventory_sessions or deps.ctx.sessions
    router: APIRouter = APIRouter(tags=["data"])

    @router.get("/api/data/assets")
    async def list_assets(
        request: Request,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DATA_READ))
        ],
    ) -> Response:
        del principal
        filters: AssetFilters = parse_filters(request.query_params, deps.redactor)
        async with data_sessions() as session:
            page: AssetPage = await deps.queries.list(session, filters, deps.ctx.clock.now().date())
        return Response(
            dump_json(
                json_object(
                    (
                        ("items", json_array(tuple(asset_json(asset) for asset in page.items))),
                        (
                            "next_cursor",
                            json_str(page.next_cursor) if page.next_cursor else json_null(),
                        ),
                    )
                )
            ),
            media_type="application/json",
        )

    @router.get("/api/data/counts")
    async def counts(
        request: Request,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DATA_READ))
        ],
    ) -> Response:
        del principal
        filters: AssetFilters = parse_filters(request.query_params, deps.redactor)
        async with data_sessions() as session:
            result: CountsView = await deps.queries.counts(
                session, filters, deps.ctx.clock.now().date()
            )
        return Response(
            dump_json(
                json_object(
                    (
                        ("all", json_int(result.all)),
                        ("present", json_int(result.present)),
                        ("deleted", json_int(result.deleted)),
                        ("incomplete", json_int(result.incomplete)),
                        ("overdue", json_int(result.overdue)),
                    )
                )
            ),
            media_type="application/json",
        )

    @router.get("/api/data/export")
    async def export_assets(
        request: Request,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DATA_READ))
        ],
    ) -> Response:
        filters: AssetFilters = parse_filters(request.query_params, deps.redactor)
        today: date = deps.ctx.clock.now().date()
        async with data_sessions.begin() as session:
            export: CsvExport = await deps.exporter.export(session, filters, today)
            await deps.audit.append(
                session,
                AuditEntry(
                    actor_kind_for(principal.actor_ref()),
                    principal.actor_ref(),
                    "data_asset.exported",
                    "data_asset",
                    "export",
                    "Data assets exported",
                    json_object(
                        (
                            ("row_count", json_int(export.row_count)),
                            ("truncated", json_bool(export.truncated)),
                            (
                                "filters",
                                json_array(
                                    tuple(json_str(name) for name in _filter_names(filters))
                                ),
                            ),
                        ),
                    ),
                ),
            )
        filename: str = f"telescreen-data-{today.strftime('%Y%m%d')}.csv"
        return Response(
            export.text,
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Telescreen-Truncated": str(export.truncated).lower(),
            },
        )

    @router.get("/api/data/resource-assets/{resource_key}")
    async def asset_for_resource(
        resource_key: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DATA_READ))
        ],
    ) -> Response:
        del principal
        if re.fullmatch(r"[0-9a-f]{64}", resource_key) is None:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid resource key")
        async with data_sessions() as session:
            asset: AssetView | None = await deps.queries.by_resource_key(
                session, resource_key, deps.ctx.clock.now().date()
            )
        return Response(
            dump_json(
                json_object((("asset", asset_json(asset) if asset is not None else json_null()),))
            ),
            media_type="application/json",
        )

    return router


def _filter_names(filters: AssetFilters) -> tuple[str, ...]:
    """Return only names of active filters for the audit record."""
    names: list[str] = []
    for name in (
        "tab",
        "q",
        "source",
        "kind",
        "state",
        "origin",
        "owner",
        "collector_config_id",
        "sort",
    ):
        if getattr(filters, name) is not None:
            names.append(name)
    if filters.fields:
        names.append("field")
    return tuple(names)
