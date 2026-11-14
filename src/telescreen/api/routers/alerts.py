"""Standalone REST API for alert rule management and fire history."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse

from telescreen.alerts.model import AlertActions, AlertFilters, AlertRule, AlertTrigger
from telescreen.alerts.service import AlertRuleService
from telescreen.alerts.validate import RuleIssue
from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.context import ApiContext
from telescreen.core.clock import ensure_utc
from telescreen.core.json import (
    JsonValue,
    dump_json,
    json_array,
    json_bool,
    json_int,
    json_null,
    json_object,
    json_str,
    parse_json,
)
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import (
    AlertTriggerKind,
    IssuePriority,
    NotificationChannel,
    Severity,
)
from telescreen.db.models import AlertFireRow, AlertRuleRow
from telescreen.workflow.dsl.model import NotificationTargetSpec


@pydantic.dataclasses.dataclass(frozen=True)
class AlertTriggerBody:
    """Trigger fields accepted by alert rule endpoints."""

    kind: AlertTriggerKind
    threshold: int | None = None
    window_minutes: int | None = None


@pydantic.dataclasses.dataclass(frozen=True)
class AlertFiltersBody:
    """Issue filter fields accepted by alert rule endpoints."""

    min_severity: Severity | None = None
    priorities: tuple[IssuePriority, ...] = ()
    sources: tuple[str, ...] = ()
    rule_classes: tuple[str, ...] = ()
    exposures: tuple[str, ...] = ()
    owner_teams: tuple[str, ...] = ()
    detectors: tuple[str, ...] = ()


@pydantic.dataclasses.dataclass(frozen=True)
class AlertTargetBody:
    """Notification target fields accepted by alert rule endpoints."""

    recipients: tuple[str, ...]
    channels: tuple[NotificationChannel, ...]
    template: str | None = None


@pydantic.dataclasses.dataclass(frozen=True)
class AlertActionsBody:
    """Notification and workflow actions accepted by alert rule endpoints."""

    targets: tuple[AlertTargetBody, ...] = ()
    start_workflow: str | None = None


@pydantic.dataclasses.dataclass(frozen=True)
class AlertRuleBody:
    """Candidate rule submitted for validation or persistence."""

    name: str
    trigger: AlertTriggerBody
    filters: AlertFiltersBody = AlertFiltersBody()
    actions: AlertActionsBody = AlertActionsBody()
    action_interval_minutes: int = 60


@pydantic.dataclasses.dataclass(frozen=True)
class MuteBody:
    """Mute duration in minutes."""

    minutes: int


@dataclass(frozen=True)
class AlertsRouterDeps:
    """Dependencies required by standalone alert routes."""

    ctx: ApiContext
    service: AlertRuleService


def build_alerts_router(deps: AlertsRouterDeps) -> APIRouter:
    """Build the standalone alert API; wiring is owned by a later plan."""
    router: APIRouter = APIRouter(tags=["alerts"])

    @router.get("/api/alert-rules")
    async def list_rules(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ALERTS_READ))
        ],
    ) -> Response:
        del principal
        async with deps.ctx.sessions() as session:
            rows: tuple[AlertRuleRow, ...] = await deps.service.list(session)
            items: list[JsonValue] = []
            for row in rows:
                count: int = await deps.service.repository.fires_since(
                    session, row.id, deps.ctx.clock.now() - timedelta(hours=24)
                )
                items.append(_rule_json(row, count, deps.ctx.clock.now()))
        return Response(
            content=dump_json(
                json_object((("items", json_array(tuple(items))), ("next_cursor", json_null())))
            ),
            media_type="application/json",
        )

    @router.post("/api/alert-rules/validate")
    async def validate(
        body: AlertRuleBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ALERTS_WRITE))
        ],
    ) -> Response:
        del principal
        async with deps.ctx.sessions() as session:
            issues: tuple[RuleIssue, ...] = await deps.service.validate(session, _rule(body))
        diagnostics: tuple[JsonValue, ...] = tuple(
            json_object((("field", json_str(issue.field)), ("code", json_str(issue.code))))
            for issue in issues
        )
        return Response(
            content=dump_json(
                json_object((("valid", json_bool(not issues)), ("errors", json_array(diagnostics))))
            ),
            media_type="application/json",
        )

    @router.post("/api/alert-rules", status_code=201)
    async def create(
        body: AlertRuleBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ALERTS_WRITE))
        ],
    ) -> Response:
        async with deps.ctx.sessions.begin() as session:
            rule: AlertRule = _rule(body)
            issues: tuple[RuleIssue, ...] = await deps.service.validate(session, rule)
            if issues:
                return JSONResponse(
                    status_code=422,
                    content={
                        "valid": False,
                        "errors": [{"field": issue.field, "code": issue.code} for issue in issues],
                    },
                )
            row: AlertRuleRow = await deps.service.create(session, rule, principal.actor_ref())
        return Response(
            status_code=201,
            content=dump_json(_rule_json(row, 0, deps.ctx.clock.now())),
            media_type="application/json",
        )

    @router.get("/api/alert-rules/{rule_id}")
    async def get_rule(
        rule_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ALERTS_READ))
        ],
    ) -> Response:
        del principal
        async with deps.ctx.sessions() as session:
            row: AlertRuleRow = await deps.service.get(session, rule_id)
            count: int = await deps.service.repository.fires_since(
                session, row.id, deps.ctx.clock.now() - timedelta(hours=24)
            )
        return Response(
            content=dump_json(_rule_json(row, count, deps.ctx.clock.now())),
            media_type="application/json",
        )

    @router.put("/api/alert-rules/{rule_id}")
    async def update(
        rule_id: str,
        body: AlertRuleBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ALERTS_WRITE))
        ],
    ) -> Response:
        async with deps.ctx.sessions.begin() as session:
            issues: tuple[RuleIssue, ...] = await deps.service.validate(session, _rule(body))
            if issues:
                return JSONResponse(
                    status_code=422,
                    content={
                        "valid": False,
                        "errors": [{"field": issue.field, "code": issue.code} for issue in issues],
                    },
                )
            row: AlertRuleRow = await deps.service.update(
                session, rule_id, _rule(body), principal.actor_ref()
            )
        return Response(
            content=dump_json(_rule_json(row, 0, deps.ctx.clock.now())),
            media_type="application/json",
        )

    @router.delete("/api/alert-rules/{rule_id}", status_code=204)
    async def delete(
        rule_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ALERTS_WRITE))
        ],
    ) -> Response:
        async with deps.ctx.sessions.begin() as session:
            await deps.service.delete(session, rule_id, principal.actor_ref())
        return Response(status_code=204)

    @router.post("/api/alert-rules/{rule_id}/enable")
    async def enable(
        rule_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ALERTS_WRITE))
        ],
    ) -> Response:
        return await _set_enabled(deps, rule_id, True, principal)

    @router.post("/api/alert-rules/{rule_id}/disable")
    async def disable(
        rule_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ALERTS_WRITE))
        ],
    ) -> Response:
        return await _set_enabled(deps, rule_id, False, principal)

    @router.post("/api/alert-rules/{rule_id}/mute")
    async def mute(
        rule_id: str,
        body: MuteBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ALERTS_MUTE))
        ],
    ) -> Response:
        async with deps.ctx.sessions.begin() as session:
            row: AlertRuleRow = await deps.service.mute(
                session, rule_id, body.minutes, principal.actor_ref()
            )
        return Response(
            content=dump_json(_rule_json(row, 0, deps.ctx.clock.now())),
            media_type="application/json",
        )

    @router.post("/api/alert-rules/{rule_id}/unmute")
    async def unmute(
        rule_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ALERTS_MUTE))
        ],
    ) -> Response:
        async with deps.ctx.sessions.begin() as session:
            row: AlertRuleRow = await deps.service.unmute(session, rule_id, principal.actor_ref())
        return Response(
            content=dump_json(_rule_json(row, 0, deps.ctx.clock.now())),
            media_type="application/json",
        )

    @router.get("/api/alert-rules/{rule_id}/history")
    async def history(
        rule_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.ALERTS_READ))
        ],
        cursor: str | None = None,
    ) -> Response:
        del principal
        async with deps.ctx.sessions() as session:
            rows: tuple[AlertFireRow, ...] = await deps.service.history(session, rule_id, cursor)
        page: tuple[AlertFireRow, ...] = rows[:50]
        next_cursor: str | None = (
            f"{ensure_utc(page[-1].fired_at).isoformat()}|{page[-1].id}"
            if len(rows) > 50 and page
            else None
        )
        entries: tuple[JsonValue, ...] = tuple(_fire_json(row) for row in page)
        return Response(
            content=dump_json(
                json_object(
                    (
                        ("items", json_array(entries)),
                        ("next_cursor", json_str(next_cursor) if next_cursor else json_null()),
                    )
                )
            ),
            media_type="application/json",
        )

    return router


async def _set_enabled(
    deps: AlertsRouterDeps, rule_id: str, enabled: bool, principal: Principal
) -> Response:
    """Persist an enabled-state change and serialize the resulting rule."""
    async with deps.ctx.sessions.begin() as session:
        row: AlertRuleRow = await deps.service.set_enabled(
            session, rule_id, enabled, principal.actor_ref()
        )
    return Response(
        content=dump_json(_rule_json(row, 0, deps.ctx.clock.now())), media_type="application/json"
    )


def _rule(body: AlertRuleBody) -> AlertRule:
    """Convert the API payload into the domain model."""
    filters: AlertFilters = AlertFilters(
        body.filters.min_severity,
        body.filters.priorities,
        body.filters.sources,
        body.filters.rule_classes,
        body.filters.exposures,
        body.filters.owner_teams,
        body.filters.detectors,
    )
    targets: tuple[NotificationTargetSpec, ...] = tuple(
        NotificationTargetSpec(item.recipients, item.channels, item.template)
        for item in body.actions.targets
    )
    actions: AlertActions = AlertActions(targets, body.actions.start_workflow)
    return AlertRule(
        body.name,
        AlertTrigger(body.trigger.kind, body.trigger.threshold, body.trigger.window_minutes),
        filters,
        actions,
        body.action_interval_minutes,
    )


def _rule_json(row: AlertRuleRow, fires_24h: int, now: datetime) -> JsonValue:
    """Serialize a rule and safe list summary fields."""
    status: str = (
        "muted"
        if row.muted_until is not None and row.muted_until > now
        else "enabled"
        if row.enabled
        else "disabled"
    )
    return json_object(
        (
            ("id", json_str(row.id)),
            ("name", json_str(row.name)),
            ("enabled", json_bool(row.enabled)),
            (
                "trigger",
                parse_json(
                    dump_json(
                        json_object(
                            (
                                ("kind", json_str(row.trigger_kind)),
                                (
                                    "threshold",
                                    json_int(row.threshold)
                                    if row.threshold is not None
                                    else json_null(),
                                ),
                                (
                                    "window_minutes",
                                    json_int(row.window_minutes)
                                    if row.window_minutes is not None
                                    else json_null(),
                                ),
                            )
                        )
                    )
                ),
            ),
            ("filters", parse_json(row.filters_json)),
            ("actions", parse_json(row.actions_json)),
            ("action_interval_minutes", json_int(row.action_interval_minutes)),
            (
                "muted_until",
                json_str(ensure_utc(row.muted_until).isoformat())
                if row.muted_until
                else json_null(),
            ),
            (
                "last_fired_at",
                json_str(ensure_utc(row.last_fired_at).isoformat())
                if row.last_fired_at
                else json_null(),
            ),
            ("fires_24h", json_int(fires_24h)),
            ("status", json_str(status)),
            ("created_at", json_str(ensure_utc(row.created_at).isoformat())),
            ("updated_at", json_str(ensure_utc(row.updated_at).isoformat())),
        )
    )


def _fire_json(row: AlertFireRow) -> JsonValue:
    """Serialize safe fire history fields."""
    return json_object(
        (
            ("id", json_str(row.id)),
            ("rule_id", json_str(row.rule_id)),
            ("issue_id", json_str(row.issue_id)),
            ("trigger_kind", json_str(row.trigger_kind)),
            ("outcome", json_str(row.outcome)),
            ("notification_count", json_int(row.notification_count)),
            ("run_id", json_str(row.run_id) if row.run_id else json_null()),
            ("error_code", json_str(row.error_code) if row.error_code else json_null()),
            ("fired_at", json_str(ensure_utc(row.fired_at).isoformat())),
        )
    )
