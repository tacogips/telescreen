"""Reporting Bureau endpoints for collector configuration and webhooks."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Protocol

import pydantic
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.common.auth import Principal, declare_external, require_permission
from telescreen.api.common.body import read_limited_body
from telescreen.api.common.context import ApiContext
from telescreen.collectors.codec import selector_from_json, selector_to_json
from telescreen.collectors.coverage import CoverageRecorder, CoverageResourceView, CoverageSummary
from telescreen.collectors.health import CollectorHealthView
from telescreen.collectors.service import CollectorService, WebhookOutcome
from telescreen.core.clock import ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import dump_json, parse_json
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import (
    CollectorKind,
    CoverageState,
    ErrorCode,
    ExternalAuthKind,
    InventoryMode,
    ScanTrigger,
    TriggerMode,
)
from telescreen.db.models import CollectorConfigRow
from telescreen.ports.collectors import (
    CollectorConfig,
    CollectorSecrets,
    CollectorSelector,
    InboundWebhook,
)
from telescreen.ports.issues import ResourceIssueReader, ResourceIssueRef
from telescreen.ports.sweeps import SweepLauncher


@dataclass(frozen=True)
class WebhookSecrets:
    """Webhook verification secrets resolved by application wiring."""

    slack: CollectorSecrets
    github: CollectorSecrets
    gdrive: CollectorSecrets

    def for_kind(self, kind: CollectorKind) -> CollectorSecrets:
        """Return the secret set for a webhook path kind."""
        if kind is CollectorKind.SLACK:
            return self.slack
        if kind is CollectorKind.GITHUB:
            return self.github
        return self.gdrive


class CollectorHealthLister(Protocol):
    """Resolve health views for stored collector rows."""

    async def list(
        self, session: AsyncSession, configs: tuple[CollectorConfigRow, ...], now: datetime
    ) -> tuple[tuple[str, CollectorHealthView], ...]:
        """Return collector health views."""
        ...


@dataclass(frozen=True)
class CollectorsRouterDeps:
    """Dependencies for collector API routes."""

    ctx: ApiContext
    service: CollectorService
    launcher: SweepLauncher
    secrets: WebhookSecrets
    coverage: CoverageRecorder | None = None
    issues: ResourceIssueReader | None = None
    health_view: CollectorHealthLister | None = None


@dataclass(frozen=True)
class CollectorHealth:
    """Safe health fields for a collector list item."""

    status: str
    conditions: tuple[str, ...]
    last_success_at: str | None
    last_failure_at: str | None
    consecutive_failures: int
    last_event_at: str | None
    watch_expires_at: str | None
    event_only: bool


@pydantic.dataclasses.dataclass(frozen=True)
class CollectorBody:
    """Request body for create and update collector configuration."""

    name: str
    kind: CollectorKind
    trigger_mode: TriggerMode
    cron: str | None
    sweep_cron: str | None
    selector_json: str
    credential_ref: str | None
    enabled: bool = True
    inventory_mode: InventoryMode | None = None


@pydantic.dataclasses.dataclass(frozen=True)
class CollectorListItem:
    """Safe fields displayed for one collector."""

    id: str
    name: str
    kind: CollectorKind
    trigger_mode: TriggerMode
    cron: str | None
    sweep_cron: str | None
    credential_ref: str | None
    enabled: bool
    inventory_mode: InventoryMode
    selector_json: str
    health: CollectorHealth | None


@pydantic.dataclasses.dataclass(frozen=True)
class CoverageCount:
    """Count of resources by scan state and reason."""

    state: str
    reason: str
    count: int


@pydantic.dataclasses.dataclass(frozen=True)
class CoverageSummaryResponse:
    """Coverage totals for a collector over a fixed seven-day window."""

    since: str
    counts: tuple[CoverageCount, ...]
    unscannable_total: int


@pydantic.dataclasses.dataclass(frozen=True)
class CoverageIssue:
    """Safe issue reference associated with a coverage resource."""

    issue_id: str
    short_id: str


@pydantic.dataclasses.dataclass(frozen=True)
class CoverageResource:
    """Safe public projection of an unscannable resource."""

    resource_id: str
    source: str
    state: str
    reason: str | None
    truncated_bytes: int
    exposure_visibility: str
    first_seen_at: str
    last_attempt_at: str
    issues: tuple[CoverageIssue, ...]


@pydantic.dataclasses.dataclass(frozen=True)
class CoverageResourcesResponse:
    """Page of safe unscannable resource metadata."""

    items: tuple[CoverageResource, ...]
    next_cursor: str | None


@pydantic.dataclasses.dataclass(frozen=True)
class CollectorListResponse:
    """List response for collector configuration."""

    items: tuple[CollectorListItem, ...]


@pydantic.dataclasses.dataclass(frozen=True)
class ConfigIdBody:
    """Safe identifier response."""

    id: str


@pydantic.dataclasses.dataclass(frozen=True)
class MapJobIdBody:
    """Map-reduce job identifier returned after a collector sweep starts."""

    map_job_id: str


def build_collectors_router(deps: CollectorsRouterDeps) -> APIRouter:
    """Build routes for collector configuration and webhook intake."""
    router: APIRouter = APIRouter(prefix="/api/collectors", tags=["collectors"])

    @router.get("", response_model=CollectorListResponse)
    async def list_configs(
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.COLLECTORS_READ))
        ],
    ) -> CollectorListResponse:
        """List safe collector configuration fields."""
        async with deps.ctx.sessions() as session:
            configs: tuple[CollectorConfig, ...] = await deps.service.list(session)
        items: tuple[CollectorListItem, ...] = tuple(
            CollectorListItem(
                config.id,
                config.name,
                config.kind,
                config.trigger_mode,
                config.cron,
                config.sweep_cron,
                config.credential_ref,
                config.enabled,
                config.inventory_mode,
                dump_json(selector_to_json(config.selector)),
                None,
            )
            for config in configs
        )
        if deps.health_view is not None:
            async with deps.ctx.sessions() as session:
                rows: tuple[CollectorConfigRow, ...] = tuple(
                    await session.scalars(
                        select(CollectorConfigRow).where(
                            CollectorConfigRow.id.in_(tuple(config.id for config in configs))
                        )
                    )
                )
                health: tuple[tuple[str, CollectorHealthView], ...] = await deps.health_view.list(
                    session, rows, deps.ctx.clock.now()
                )
            items = tuple(
                CollectorListItem(
                    item.id,
                    item.name,
                    item.kind,
                    item.trigger_mode,
                    item.cron,
                    item.sweep_cron,
                    item.credential_ref,
                    item.enabled,
                    item.inventory_mode,
                    item.selector_json,
                    _health_for_item(health, item.id),
                )
                for item in items
            )
        return CollectorListResponse(items)

    @router.get("/{config_id}/coverage", response_model=CoverageSummaryResponse)
    async def coverage_summary(
        config_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.COLLECTORS_READ))
        ],
    ) -> CoverageSummaryResponse:
        """Return seven-day scan coverage totals for a collector."""
        del principal
        if deps.coverage is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "collector not found")
        async with deps.ctx.sessions() as session:
            if await deps.service.get(session, config_id) is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "collector not found")
            since: datetime = ensure_utc(deps.ctx.clock.now()) - timedelta(days=7)
            summary: CoverageSummary = await deps.coverage.summary(session, config_id, since)
        return CoverageSummaryResponse(
            since.isoformat(),
            tuple(CoverageCount(state, reason, count) for state, reason, count in summary.counts),
            summary.unscannable_total,
        )

    @router.get("/{config_id}/coverage/resources", response_model=CoverageResourcesResponse)
    async def coverage_resources(
        config_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.COLLECTORS_READ))
        ],
        state: CoverageState | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> CoverageResourcesResponse:
        """List incomplete resources without exposing their keyed locators."""
        del principal
        if deps.coverage is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "collector not found")
        async with deps.ctx.sessions() as session:
            if await deps.service.get(session, config_id) is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "collector not found")
            views: tuple[CoverageResourceView, ...]
            next_cursor: str | None
            views, next_cursor = await deps.coverage.resources(
                session, config_id, state, limit, cursor
            )
            issue_refs: tuple[ResourceIssueRef, ...] = (
                await deps.issues.open_issues_for_resources(
                    session, tuple(view.resource_key for view in views)
                )
                if deps.issues is not None and views
                else ()
            )
        items: tuple[CoverageResource, ...] = tuple(
            CoverageResource(
                view.resource_key[:12],
                view.source,
                view.state,
                view.reason,
                view.truncated_bytes,
                view.exposure_visibility,
                ensure_utc(view.first_seen_at).isoformat(),
                ensure_utc(view.last_attempt_at).isoformat(),
                _issues_for_resource(issue_refs, view.resource_key),
            )
            for view in views
        )
        return CoverageResourcesResponse(items, next_cursor)

    @router.post("", response_model=ConfigIdBody)
    async def create_config(
        body: CollectorBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.COLLECTORS_WRITE))
        ],
    ) -> ConfigIdBody:
        """Create a collector config from a JSON selector."""
        selector: CollectorSelector = selector_from_json(body.kind, parse_json(body.selector_json))
        async with deps.ctx.sessions.begin() as session:
            config: CollectorConfig = await deps.service.create(
                session,
                body.name,
                body.kind,
                body.trigger_mode,
                body.cron,
                body.sweep_cron,
                selector,
                body.credential_ref,
                principal.actor_ref(),
                body.inventory_mode,
            )
        return ConfigIdBody(config.id)

    @router.patch("/{config_id}", response_model=ConfigIdBody)
    async def update_config(
        config_id: str,
        body: CollectorBody,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.COLLECTORS_WRITE))
        ],
    ) -> ConfigIdBody:
        """Update an existing collector config."""
        selector: CollectorSelector = selector_from_json(body.kind, parse_json(body.selector_json))
        async with deps.ctx.sessions.begin() as session:
            config: CollectorConfig = await deps.service.update(
                session,
                config_id,
                body.trigger_mode,
                body.cron,
                body.sweep_cron,
                selector,
                body.credential_ref,
                body.enabled,
                principal.actor_ref(),
                body.inventory_mode,
            )
        return ConfigIdBody(config.id)

    @router.post("/{config_id}/run", response_model=ConfigIdBody)
    async def run_config(
        config_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.COLLECTORS_WRITE))
        ],
    ) -> ConfigIdBody:
        """Queue a manual incremental scan."""
        async with deps.ctx.sessions.begin() as session:
            await deps.service.enqueue_run(session, config_id, ScanTrigger.MANUAL)
        return ConfigIdBody(config_id)

    @router.post("/{config_id}/sweep", response_model=MapJobIdBody)
    async def sweep_config(
        config_id: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.COLLECTORS_WRITE))
        ],
    ) -> MapJobIdBody:
        """Launch a map-reduce collector sweep."""
        async with deps.ctx.sessions() as session:
            config: CollectorConfig | None = await deps.service.get(session, config_id)
        if config is None:
            from telescreen.core.errors import TelescreenError
            from telescreen.core.vocabulary import ErrorCode

            raise TelescreenError(ErrorCode.NOT_FOUND, "collector not found")
        job_id: str = await deps.launcher.start_collector_sweep(config_id, ScanTrigger.MANUAL)
        return MapJobIdBody(job_id)

    @router.post(
        "/{kind}/webhook",
        dependencies=[Depends(declare_external(ExternalAuthKind.PROVIDER_SIGNATURE))],
    )
    async def webhook(kind: CollectorKind, request: Request) -> Response:
        """Verify and enqueue a source webhook without scanning inline."""
        body: bytes = await read_limited_body(request, 1024 * 1024)
        event: InboundWebhook = InboundWebhook(
            kind,
            request.method,
            deps.ctx.clock.now(),
            tuple((key, value) for key, value in request.headers.items()),
            body,
        )
        async with deps.ctx.sessions.begin() as session:
            outcome: WebhookOutcome = await deps.service.handle_webhook(
                session, kind, event, deps.secrets.for_kind(kind)
            )
        return Response(content=outcome.body or "", status_code=outcome.status)

    return router


def _health_json(view: CollectorHealthView) -> CollectorHealth:
    """Build a safe serialized collector health view."""
    return CollectorHealth(
        view.status.value,
        tuple(condition.value for condition in view.conditions),
        _iso(view.last_success_at),
        _iso(view.last_failure_at),
        view.consecutive_failures,
        _iso(view.last_event_at),
        _iso(view.watch_expires_at),
        view.event_only,
    )


def _health_for(
    health: tuple[tuple[str, CollectorHealthView], ...], config_id: str
) -> CollectorHealthView | None:
    """Find a collector health view by config ID."""
    return next((view for candidate, view in health if candidate == config_id), None)


def _health_for_item(
    health: tuple[tuple[str, CollectorHealthView], ...], config_id: str
) -> CollectorHealth | None:
    """Find and serialize health for a collector list item."""
    view: CollectorHealthView | None = _health_for(health, config_id)
    return _health_json(view) if view is not None else None


def _issues_for_resource(
    issues: tuple[ResourceIssueRef, ...], resource_key: str
) -> tuple[CoverageIssue, ...]:
    """Project issue refs for one resource without exposing its keyed reference."""
    return tuple(
        CoverageIssue(issue.issue_id, issue.short_id)
        for issue in issues
        if issue.resource_key == resource_key
    )


def _iso(value: datetime | None) -> str | None:
    """Serialize an optional timestamp as UTC ISO-8601."""
    return ensure_utc(value).isoformat() if value is not None else None
