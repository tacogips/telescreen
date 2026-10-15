"""Standalone inventory composition with no main database credentials or services."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

import httpx
from fastapi import FastAPI, Request
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from telescreen.api.common.context import ApiContext
from telescreen.api.common.errors import install_error_handlers
from telescreen.api.routers.data_asset_detail import (
    DataAssetDetailRouterDeps,
    build_data_asset_detail_router,
)
from telescreen.api.routers.data_asset_values import (
    DataAssetValuesRouterDeps,
    build_data_asset_values_router,
)
from telescreen.api.routers.data_assets import DataAssetsRouterDeps, build_data_assets_router
from telescreen.api.routers.data_fields import DataFieldsRouterDeps, build_data_fields_router
from telescreen.core.clock import Clock, SystemClock
from telescreen.core.config import TelescreenSettings
from telescreen.core.errors import ConfigError, TelescreenError
from telescreen.core.secrets import EnvSource, SecretValue, resolve_secret
from telescreen.db.session import create_engine, create_sessionmaker
from telescreen.inventory.assets import AssetService
from telescreen.inventory.audit import InventoryAuditOutbox, InventoryAuditPublisher
from telescreen.inventory.export import AssetExporter
from telescreen.inventory.fields import FieldService
from telescreen.inventory.handoff.apply import InventoryOperationApplier
from telescreen.inventory.handoff.inbox import InventoryInbox
from telescreen.inventory.observer import InventoryObserverImpl
from telescreen.inventory.queries import AssetQueries
from telescreen.inventory.reconcile import InventoryReconcilerImpl
from telescreen.inventory.registration import RegistrationService
from telescreen.inventory.repository import InventoryRepository
from telescreen.inventory.retention import DataOverdueHandler
from telescreen.inventory.retention_outbox import InventoryRetentionLookup, RetentionOutbox
from telescreen.inventory.values import ValueService, ValueValidator
from telescreen.inventory_service.clients import AuditClient, BureauClient, RetentionClient
from telescreen.inventory_service.transport import (
    PREFIX,
    InternalAuth,
    ServiceHttp,
    encode_response,
    read_payload,
)
from telescreen.inventory_service.worker import InventoryWorker, OutboxPump
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.audit_commands import AuditCommand, AuditReceipt
from telescreen.ports.inventory_commands import InventoryReceipt, decode_inventory_command
from telescreen.ports.retention import (
    RetentionCommand,
    RetentionReceipt,
    RetentionStatus,
    decode_retention_command,
)


@dataclass(frozen=True)
class InventoryRuntime:
    """Inventory resources; callers own an injected session factory and HTTP client."""

    sessions: async_sessionmaker[AsyncSession]
    engine: AsyncEngine | None
    worker: InventoryWorker
    context: ApiContext
    inbox: InventoryInbox
    retention: InventoryRetentionLookup
    queries: AssetQueries
    assets: AssetService
    fields: FieldService
    validator: ValueValidator
    values: ValueService
    registration: RegistrationService
    audit: InventoryAuditPublisher
    redactor: Redactor
    auth: InternalAuth


def build_inventory_runtime(
    settings: TelescreenSettings,
    env: EnvSource,
    http: httpx.AsyncClient,
    sessions: async_sessionmaker[AsyncSession] | None = None,
    clock: Clock | None = None,
) -> InventoryRuntime:
    """Require only inventory persistence, fingerprint keys and the bureau callback."""
    if not settings.inventory_database_url or not settings.bureau_service_url:
        raise ConfigError("inventory database URL and bureau service URL are required")
    if not settings.inventory_service_token_ref:
        raise ConfigError("inventory service token is required")
    secret: SecretValue = resolve_secret(settings.inventory_service_token_ref, env)
    transport: ServiceHttp = ServiceHttp(http, settings.bureau_service_url, secret)
    bureau: BureauClient = BureauClient(transport)
    keys: tuple[tuple[int, SecretValue], ...] = tuple(
        (item.version, resolve_secret(item.ref, env)) for item in settings.fingerprint_keys
    )
    if not keys and settings.fingerprint_key_ref:
        keys = ((1, resolve_secret(settings.fingerprint_key_ref, env)),)
    if not keys:
        raise ConfigError("inventory fingerprint key is required")
    fingerprinter: Fingerprinter = Fingerprinter.from_versions(
        keys, settings.fingerprint_active_version
    )
    selected_clock: Clock = clock or SystemClock()
    redactor: Redactor = Redactor.default()
    engine: AsyncEngine | None = None
    if sessions is None:
        engine = create_engine(settings.inventory_database_url)
        sessions = create_sessionmaker(engine)
    repository: InventoryRepository = InventoryRepository()
    audit: InventoryAuditPublisher = InventoryAuditPublisher(selected_clock, redactor)
    context: ApiContext = ApiContext(
        sessions, selected_clock, audit, authentication=bureau.authenticate
    )
    queries: AssetQueries = AssetQueries(bureau, bureau)
    assets: AssetService = AssetService(repository, audit, redactor, selected_clock)
    fields: FieldService = FieldService(audit, redactor, selected_clock)
    validator: ValueValidator = ValueValidator(bureau, redactor)
    values: ValueService = ValueService(repository, validator, audit, selected_clock)
    registration: RegistrationService = RegistrationService(
        repository, values, fingerprinter, redactor, audit, selected_clock
    )
    inbox: InventoryInbox = InventoryInbox(
        sessions,
        InventoryOperationApplier(
            InventoryObserverImpl(repository), InventoryReconcilerImpl(repository, selected_clock)
        ),
        selected_clock,
    )
    audit_pump: OutboxPump[AuditCommand, AuditReceipt] = OutboxPump(
        sessions, InventoryAuditOutbox(), AuditClient(transport).receive, selected_clock
    )
    retention_pump: OutboxPump[RetentionCommand, RetentionReceipt] = OutboxPump(
        sessions, RetentionOutbox(), RetentionClient(transport).receive, selected_clock
    )
    worker: InventoryWorker = InventoryWorker(
        audit_pump.tick,
        retention_pump.tick,
        DataOverdueHandler(sessions, redactor, selected_clock, settings.public_base_url),
        selected_clock,
        settings.data_overdue_cron or "",
        settings.worker_poll_seconds,
    )
    return InventoryRuntime(
        sessions,
        engine,
        worker,
        context,
        inbox,
        InventoryRetentionLookup(sessions, selected_clock),
        queries,
        assets,
        fields,
        validator,
        values,
        registration,
        audit,
        redactor,
        InternalAuth(secret),
    )


def create_inventory_app(
    runtime: InventoryRuntime, http: httpx.AsyncClient | None = None
) -> FastAPI:
    """Serve public inventory routes behind service auth and run owned background work."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        del app
        task: asyncio.Task[None] = asyncio.create_task(runtime.worker.run())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            try:
                if runtime.engine is not None:
                    await runtime.engine.dispose()
            finally:
                if http is not None:
                    await http.aclose()

    app: FastAPI = FastAPI(title="Telescreen Inventory", lifespan=lifespan)
    install_error_handlers(app)

    @app.middleware("http")
    async def service_auth(request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            runtime.auth.check(request)
        except TelescreenError:
            return JSONResponse(
                {"error": {"code": "unauthorized", "detail": "service authentication required"}},
                status_code=401,
            )
        return await call_next(request)

    app.include_router(
        build_data_assets_router(
            DataAssetsRouterDeps(
                runtime.context,
                runtime.queries,
                AssetExporter(runtime.queries, runtime.redactor),
                runtime.audit,
                runtime.redactor,
            )
        )
    )
    app.include_router(
        build_data_asset_detail_router(
            DataAssetDetailRouterDeps(runtime.context, runtime.queries, runtime.assets)
        )
    )
    app.include_router(
        build_data_fields_router(
            DataFieldsRouterDeps(runtime.context, runtime.fields, runtime.validator)
        )
    )
    app.include_router(
        build_data_asset_values_router(
            DataAssetValuesRouterDeps(
                runtime.context, runtime.values, runtime.registration, runtime.queries
            )
        )
    )

    @app.post(PREFIX + "/commands")
    async def commands(request: Request) -> Response:
        return encode_response(
            await runtime.inbox.receive(decode_inventory_command(await read_payload(request))),
            TypeAdapter(InventoryReceipt),
        )

    @app.post(PREFIX + "/retention-status")
    async def retention_status(request: Request) -> Response:
        return encode_response(
            await runtime.retention.status(decode_retention_command(await read_payload(request))),
            TypeAdapter(RetentionStatus),
        )

    @app.get(PREFIX + "/health")
    async def health() -> Response:
        return Response('"ok"', media_type="application/json")

    return app
