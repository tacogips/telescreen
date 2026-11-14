"""Unregistered dashboard overview endpoint."""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.context import ApiContext
from telescreen.core.clock import ensure_utc
from telescreen.core.json import (
    JsonValue,
    dump_json,
    json_array,
    json_int,
    json_null,
    json_object,
    json_str,
)
from telescreen.core.permissions import Permission
from telescreen.issues.dashboard import DashboardData, DashboardService
from telescreen.issues.query.period import Period, parse_period


@dataclass(frozen=True)
class DashboardRouterDeps:
    """Services required by the dashboard route."""

    ctx: ApiContext
    dashboard: DashboardService


def build_dashboard_router(deps: DashboardRouterDeps) -> APIRouter:
    """Build a dashboard router for later composition by the wiring plan."""
    router: APIRouter = APIRouter()

    @router.get("/api/dashboard")
    async def get_dashboard(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ISSUES_READ))
        ],
        period: str = "14d",
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> Response:
        """Return safe dashboard metrics for the selected time range."""
        del principal
        now: datetime = ensure_utc(deps.ctx.clock.now())
        selected: Period = parse_period(period, start, end, now)
        async with deps.ctx.sessions() as session:
            data: DashboardData = await deps.dashboard.dashboard(session, selected, now)
        return Response(content=dump_json(_dashboard_json(data)), media_type="application/json")

    return router


def _dashboard_json(data: DashboardData) -> JsonValue:
    """Serialize only dashboard counts, enums and masked titles."""
    return json_object(
        (
            (
                "buckets",
                json_array(
                    tuple(
                        json_object(
                            (
                                ("start", json_str(item.start.isoformat())),
                                ("critical", json_int(item.critical)),
                                ("high", json_int(item.high)),
                                ("medium", json_int(item.medium)),
                                ("new_issues", json_int(item.new_issues)),
                                ("resolved_issues", json_int(item.resolved_issues)),
                            )
                        )
                        for item in data.buckets
                    )
                ),
            ),
            ("open_issues", json_int(data.open_issues)),
            ("open_by_exposure", _counts(data.open_by_exposure)),
            ("open_by_source", _counts(data.open_by_source)),
            ("archived_count", json_int(data.archived_count)),
            ("escalating_count", json_int(data.escalating_count)),
            (
                "top_issues",
                json_array(
                    tuple(
                        json_object(
                            (
                                ("id", json_str(item.id)),
                                ("short_id", json_str(item.short_id)),
                                ("display_title", json_str(item.display_title)),
                                ("findings", json_int(item.findings)),
                            )
                        )
                        for item in data.top_issues
                    )
                ),
            ),
            (
                "mttr_seconds",
                json_int(data.mttr_seconds) if data.mttr_seconds is not None else json_null(),
            ),
            (
                "mtta_seconds",
                json_int(data.mtta_seconds) if data.mtta_seconds is not None else json_null(),
            ),
            ("unassigned_high_priority", json_int(data.unassigned_high_priority)),
        )
    )


def _counts(values: tuple[tuple[str, int], ...]) -> JsonValue:
    """Serialize grouped counts as a list of enum and count pairs."""
    return json_array(
        tuple(
            json_object((("key", json_str(key)), ("count", json_int(count))))
            for key, count in values
        )
    )
