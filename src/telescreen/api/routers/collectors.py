"""Reporting Bureau endpoints for collector configuration and webhooks."""

from dataclasses import dataclass
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends, Request, Response

from telescreen.api.common.auth import Principal, require_scope
from telescreen.api.common.body import read_limited_body
from telescreen.api.common.context import ApiContext
from telescreen.collectors.codec import selector_from_json, selector_to_json
from telescreen.collectors.service import CollectorService, WebhookOutcome
from telescreen.core.json import dump_json, parse_json
from telescreen.core.vocabulary import ApiScope, CollectorKind, ScanTrigger, TriggerMode
from telescreen.ports.collectors import (
    CollectorConfig,
    CollectorSecrets,
    CollectorSelector,
    InboundWebhook,
)
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


@dataclass(frozen=True)
class CollectorsRouterDeps:
    """Dependencies for collector API routes."""

    ctx: ApiContext
    service: CollectorService
    launcher: SweepLauncher
    secrets: WebhookSecrets


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
    selector_json: str


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
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.READ))],
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
                dump_json(selector_to_json(config.selector)),
            )
            for config in configs
        )
        return CollectorListResponse(items)

    @router.post("", response_model=ConfigIdBody)
    async def create_config(
        body: CollectorBody,
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.ADMIN))],
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
            )
        return ConfigIdBody(config.id)

    @router.patch("/{config_id}", response_model=ConfigIdBody)
    async def update_config(
        config_id: str,
        body: CollectorBody,
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.ADMIN))],
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
            )
        return ConfigIdBody(config.id)

    @router.post("/{config_id}/run", response_model=ConfigIdBody)
    async def run_config(
        config_id: str,
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.ADMIN))],
    ) -> ConfigIdBody:
        """Queue a manual incremental scan."""
        async with deps.ctx.sessions.begin() as session:
            await deps.service.enqueue_run(session, config_id, ScanTrigger.MANUAL)
        return ConfigIdBody(config_id)

    @router.post("/{config_id}/sweep", response_model=MapJobIdBody)
    async def sweep_config(
        config_id: str,
        principal: Annotated[Principal, Depends(require_scope(deps.ctx, ApiScope.ADMIN))],
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

    @router.post("/{kind}/webhook")
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
