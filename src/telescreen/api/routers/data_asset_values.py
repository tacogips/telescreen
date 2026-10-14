"""Asset metadata update and manual registration routes."""

from dataclasses import dataclass, field
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.context import ApiContext
from telescreen.api.routers.data_common import asset_detail_json
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, dump_json
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import DataAssetKind, ErrorCode
from telescreen.db.models import DataAssetRow, DataFieldRow
from telescreen.inventory.queries import AssetDetailView, AssetQueries
from telescreen.inventory.registration import RegistrationBody, RegistrationService
from telescreen.inventory.values import ValueInput, ValueService


@pydantic.dataclasses.dataclass(frozen=True)
class DataAssetValueBody:
    """One metadata value update."""

    key: str
    value_json: str | None


@pydantic.dataclasses.dataclass(frozen=True)
class DataAssetValuesBody:
    """Atomic metadata value update body."""

    values: list[DataAssetValueBody]


@pydantic.dataclasses.dataclass(frozen=True)
class RegistrationBodyApi:
    """Manual asset registration request."""

    name: str
    source: str
    locator: str = field(repr=False)
    kind: DataAssetKind
    item_count: int | None = None
    values: list[DataAssetValueBody] = pydantic.Field(default_factory=list)


@dataclass(frozen=True)
class DataAssetValuesRouterDeps:
    """Services required by field value and registration routes."""

    ctx: ApiContext
    values: ValueService
    registration: RegistrationService
    queries: AssetQueries
    inventory_sessions: async_sessionmaker[AsyncSession] | None = None


def build_data_asset_values_router(deps: DataAssetValuesRouterDeps) -> APIRouter:
    """Build value update and manual registration routes."""
    data_sessions: async_sessionmaker[AsyncSession] = deps.inventory_sessions or deps.ctx.sessions
    router: APIRouter = APIRouter(tags=["data"])

    @router.put("/api/data/assets/{asset_id}/fields")
    async def put_values(
        asset_id: str,
        body: DataAssetValuesBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DATA_WRITE))
        ],
    ) -> Response:
        """Validate and atomically replace supplied field values."""
        values: tuple[ValueInput, ...] = tuple(
            ValueInput(item.key, item.value_json) for item in body.values
        )
        async with data_sessions.begin() as session:
            asset: DataAssetRow | None = await session.get(
                DataAssetRow, asset_id, with_for_update=True
            )
            if asset is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "data asset not found")
            fields: tuple[DataFieldRow, ...] = tuple(
                (await session.scalars(select(DataFieldRow))).all()
            )
            await deps.values.put(session, asset, fields, values, principal.actor_ref())
            view: AssetDetailView | None = await deps.queries.detail(
                session, asset_id, deps.ctx.clock.now().date()
            )
            if view is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "data asset not found")
            content: JsonValue = asset_detail_json(view)
        return Response(dump_json(content), media_type="application/json")

    @router.post("/api/data/assets", status_code=201)
    async def register_asset(
        body: RegistrationBodyApi,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.DATA_WRITE))
        ],
    ) -> Response:
        """Register one asset and return its full detail response."""
        values: tuple[ValueInput, ...] = tuple(
            ValueInput(item.key, item.value_json) for item in body.values
        )
        request: RegistrationBody = RegistrationBody(
            body.name, body.source, body.locator, body.kind, body.item_count, values
        )
        async with data_sessions.begin() as session:
            asset_id: str = await deps.registration.register(
                session, request, principal.actor_ref()
            )
            view: AssetDetailView | None = await deps.queries.detail(
                session, asset_id, deps.ctx.clock.now().date()
            )
            if view is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "data asset not found")
            content: JsonValue = asset_detail_json(view)
        return Response(dump_json(content), status_code=201, media_type="application/json")

    return router
