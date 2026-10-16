"""Standalone inventory composition with local replicas and no bureau callback."""

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
from telescreen.audit.chain import INVENTORY_AUDIT_MODELS
from telescreen.audit.service import AuditLogService
from telescreen.core.clock import Clock, SystemClock
from telescreen.core.config import TelescreenSettings
from telescreen.core.errors import ConfigError, TelescreenError
from telescreen.core.secrets import EnvSource, SecretValue, resolve_secret
from telescreen.db.models.projections import InventoryDirectoryEntryRow
from telescreen.db.session import create_engine, create_sessionmaker
from telescreen.identity.discovery import OidcProviderClient
from telescreen.identity.verifier import OidcBearerVerifier
from telescreen.inventory.assets import AssetService
from telescreen.inventory.audit import InventoryAuditPublisher
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
from telescreen.inventory.retention_outbox import RetentionOutbox
from telescreen.inventory.values import ValueService, ValueValidator
from telescreen.inventory_service.audit_drain import LocalAuditDrain, build_local_audit_drain
from telescreen.inventory_service.dispatch import RetentionDispatcher
from telescreen.inventory_service.links import IssueLinkStore, ReplicaDirectorySink
from telescreen.inventory_service.lookups import (
    ReplicaActorLookup,
    ReplicaAssetOwnerLookup,
    ReplicaAssigneeLookup,
    ReplicaIssueLookup,
)
from telescreen.inventory_service.notify_outbox import (
    NotificationOutbox,
    NotificationOutboxPump,
    notification_transport,
)
from telescreen.inventory_service.transport import (
    PREFIX,
    InternalAuth,
    ServiceHttp,
    encode_response,
    read_payload,
)
from telescreen.inventory_service.worker import InventoryWorker
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.inventory_commands import InventoryReceipt, decode_inventory_command
from telescreen.replica.auth import ReplicaAuthenticator
from telescreen.replica.directory import (
    ReplicaActorDirectory,
    ReplicaOwnerDirectory,
    ReplicaRecipientResolver,
)
from telescreen.replica.rules import ReplicaOwnershipRules
from telescreen.replica.store import DirectoryReplica


@dataclass(frozen=True)
class InventoryRuntime:
    """Inventory resources; callers own an injected session factory and HTTP client."""

    sessions: async_sessionmaker[AsyncSession]
    engine: AsyncEngine | None
    worker: InventoryWorker
    context: ApiContext
    inbox: InventoryInbox
    queries: AssetQueries
    assets: AssetService
    fields: FieldService
    validator: ValueValidator
    values: ValueService
    registration: RegistrationService
    audit: InventoryAuditPublisher
    redactor: Redactor
    auth: InternalAuth
    replica: DirectoryReplica


def build_inventory_runtime(
    settings: TelescreenSettings,
    env: EnvSource,
    http: httpx.AsyncClient,
    sessions: async_sessionmaker[AsyncSession] | None = None,
    clock: Clock | None = None,
) -> InventoryRuntime:
    """Build inventory from its own store, directory replica and Notification client."""
    if not settings.inventory_database_url:
        raise ConfigError("inventory database URL is required")
    if not settings.inventory_service_token_ref:
        raise ConfigError("inventory service token is required")
    if not settings.notification_service_url:
        raise ConfigError("notification service URL is required")
    if not settings.notification_inventory_token_ref:
        raise ConfigError("notification inventory token is required")
    inventory_secret: SecretValue = resolve_secret(settings.inventory_service_token_ref, env)
    notification_secret: SecretValue = resolve_secret(
        settings.notification_inventory_token_ref, env
    )
    notification_http: ServiceHttp = notification_transport(
        http, settings.notification_service_url, notification_secret
    )
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
    replica: DirectoryReplica = DirectoryReplica(InventoryDirectoryEntryRow)
    verifier: OidcBearerVerifier | None = None
    if settings.oidc is not None:
        provider: OidcProviderClient = OidcProviderClient(http, settings.oidc, selected_clock)
        verifier = OidcBearerVerifier(provider, settings.oidc, selected_clock)
    recipients: ReplicaRecipientResolver = ReplicaRecipientResolver(replica)
    owners: ReplicaOwnerDirectory = ReplicaOwnerDirectory(replica)
    actor_directory: ReplicaActorDirectory = ReplicaActorDirectory(replica)
    rules: ReplicaOwnershipRules = ReplicaOwnershipRules(replica)
    issue_lookup: ReplicaIssueLookup = ReplicaIssueLookup(sessions)
    actor_lookup: ReplicaActorLookup = ReplicaActorLookup(sessions, actor_directory)
    assignee_lookup: ReplicaAssigneeLookup = ReplicaAssigneeLookup(sessions, recipients)
    fallback_lookup: ReplicaAssetOwnerLookup = ReplicaAssetOwnerLookup(sessions, owners, rules)
    repository: InventoryRepository = InventoryRepository()
    audit: InventoryAuditPublisher = InventoryAuditPublisher(selected_clock, redactor)
    context: ApiContext = ApiContext(
        sessions,
        selected_clock,
        audit,
        authentication=ReplicaAuthenticator(sessions, replica, verifier, fingerprinter),
    )
    queries: AssetQueries = AssetQueries(issue_lookup, actor_lookup)
    assets: AssetService = AssetService(repository, audit, redactor, selected_clock)
    fields: FieldService = FieldService(audit, redactor, selected_clock)
    validator: ValueValidator = ValueValidator(assignee_lookup, redactor)
    values: ValueService = ValueService(repository, validator, audit, selected_clock)
    registration: RegistrationService = RegistrationService(
        repository, values, fingerprinter, redactor, audit, selected_clock
    )
    inbox: InventoryInbox = InventoryInbox(
        sessions,
        InventoryOperationApplier(
            InventoryObserverImpl(repository),
            InventoryReconcilerImpl(repository, selected_clock),
            IssueLinkStore(),
            ReplicaDirectorySink(replica),
        ),
        selected_clock,
    )
    audit_drain: LocalAuditDrain = build_local_audit_drain(sessions, selected_clock, redactor)
    retention_dispatch: RetentionDispatcher = RetentionDispatcher(
        sessions,
        RetentionOutbox(),
        NotificationOutbox(),
        recipients,
        owners,
        fallback_lookup,
        redactor,
        AuditLogService(selected_clock, redactor, INVENTORY_AUDIT_MODELS),
        selected_clock,
    )
    worker: InventoryWorker = InventoryWorker(
        audit_drain.tick,
        retention_dispatch.tick,
        NotificationOutboxPump(
            sessions, NotificationOutbox(), notification_http, selected_clock
        ).tick,
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
        queries,
        assets,
        fields,
        validator,
        values,
        registration,
        audit,
        redactor,
        InternalAuth(inventory_secret),
        replica,
    )


def create_inventory_app(
    runtime: InventoryRuntime, http: httpx.AsyncClient | None = None
) -> FastAPI:
    """Serve unchanged inventory routes behind service authentication."""

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

    @app.get(PREFIX + "/health")
    async def health() -> Response:
        async with runtime.sessions() as session:
            revision: int = await runtime.replica.watermark(session)
        return Response(
            f'{{"status":"ok","directory_revision":{revision}}}',
            media_type="application/json",
        )

    return app
