"""Standalone Notification HTTP application and service runtime."""

import asyncio
import hmac
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
from telescreen.api.routers.notification_settings import (
    NotificationSettingsRouterDeps,
    build_notification_settings_router,
)
from telescreen.api.routers.notification_templates import (
    NotificationTemplatesRouterDeps,
    build_notification_templates_router,
)
from telescreen.audit.chain import NOTIFICATION_AUDIT_MODELS
from telescreen.audit.service import AuditLogService
from telescreen.core.clock import Clock, SystemClock
from telescreen.core.config import TelescreenSettings
from telescreen.core.errors import ConfigError, TelescreenError
from telescreen.core.json import dump_json, json_bool, json_int, json_null, json_object, json_str
from telescreen.core.secrets import EnvSource, SecretValue, resolve_secret
from telescreen.core.vocabulary import ErrorCode
from telescreen.db.models import NotificationDirectoryEntryRow
from telescreen.db.session import create_engine, create_sessionmaker
from telescreen.detection.factory import build_detector
from telescreen.detection.types import Detector
from telescreen.identity.discovery import OidcProviderClient
from telescreen.identity.verifier import OidcBearerVerifier
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.notification_service.inbox import NotificationInbox, Producer
from telescreen.notification_service.runtime import (
    NotificationTaskQueue,
    ReplicaMemberReferences,
    ReplicaPreferenceRepository,
)
from telescreen.notification_service.worker import DeliveryPump
from telescreen.notifications.delivery import NotificationDeliveryHandler
from telescreen.notifications.dlp import DlpGate
from telescreen.notifications.email import EmailNotifier, SmtplibTransport
from telescreen.notifications.preferences import PreferenceRepository, PreferenceService
from telescreen.notifications.repository import NotificationRepository
from telescreen.notifications.sender import NotificationService
from telescreen.notifications.slack import SlackNotifier
from telescreen.notifications.templates.repository import TemplateRepository
from telescreen.notifications.templates.resolver import DbTemplateResolver
from telescreen.notifications.templates.service import TemplateDetail, TemplateService
from telescreen.notifications.tester import NotificationServiceTester
from telescreen.ports.identity import BearerVerifier
from telescreen.ports.notification_commands import (
    ChannelStatus,
    NotificationCommandReceipt,
    TestRequest,
    decode_notification_command,
)
from telescreen.ports.notifications import TestOutcome
from telescreen.ports.templates import TemplateInfo
from telescreen.replica.auth import ReplicaAuthenticator
from telescreen.replica.directory import ReplicaOwnerDirectory, ReplicaRecipientResolver
from telescreen.replica.store import DirectoryReplica

NOTIFICATION_PREFIX: str = "/internal/notification/v1"
SERVICE_HEADER: str = "x-telescreen-service-token"
MAX_REQUEST_BYTES: int = 1_048_576


async def _read_payload(request: Request) -> str:
    """Read one bounded UTF-8 JSON payload without exposing request content."""
    declared: str | None = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_REQUEST_BYTES:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "request exceeds limit")
    chunks: list[bytes] = []
    size: int = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_REQUEST_BYTES:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "request exceeds limit")
        chunks.append(chunk)
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid request") from None


@dataclass(frozen=True)
class NotificationRuntime:
    """Resources owned by one Notification process."""

    sessions: async_sessionmaker[AsyncSession]
    engine: AsyncEngine | None
    pump: DeliveryPump
    inbox: NotificationInbox
    context: ApiContext
    templates: TemplateService
    preferences: PreferenceService
    tester: NotificationServiceTester
    inventory_secret: SecretValue
    telescreen_secret: SecretValue
    channels: ChannelStatus
    replica: DirectoryReplica


def build_notification_runtime(
    settings: TelescreenSettings,
    env: EnvSource,
    http: httpx.AsyncClient,
    sessions: async_sessionmaker[AsyncSession] | None = None,
    clock: Clock | None = None,
) -> NotificationRuntime:
    """Construct all Notification-owned resources and resolve delivery credentials here."""
    if not settings.notification_database_url:
        raise ConfigError("notification database URL is required")
    if not settings.notification_service_token_ref or not settings.notification_inventory_token_ref:
        raise ConfigError("notification service credentials are required")
    telescreen_secret: SecretValue = resolve_secret(settings.notification_service_token_ref, env)
    inventory_secret: SecretValue = resolve_secret(settings.notification_inventory_token_ref, env)
    selected_clock: Clock = clock or SystemClock()
    redactor: Redactor = Redactor.default()
    engine: AsyncEngine | None = None
    if sessions is None:
        engine = create_engine(settings.notification_database_url)
        sessions = create_sessionmaker(engine)
    replica: DirectoryReplica = DirectoryReplica(NotificationDirectoryEntryRow)
    recipients: ReplicaRecipientResolver = ReplicaRecipientResolver(replica)
    fingerprint_values: tuple[tuple[int, SecretValue], ...] = tuple(
        (item.version, resolve_secret(item.ref, env)) for item in settings.fingerprint_keys
    )
    if not fingerprint_values and settings.fingerprint_key_ref:
        fingerprint_values = ((1, resolve_secret(settings.fingerprint_key_ref, env)),)
    fingerprinter: Fingerprinter | None = (
        Fingerprinter.from_versions(fingerprint_values, settings.fingerprint_active_version)
        if fingerprint_values
        else None
    )
    verifier: BearerVerifier | None = None
    if settings.oidc is not None:
        verifier = OidcBearerVerifier(
            OidcProviderClient(http, settings.oidc, selected_clock),
            settings.oidc,
            selected_clock,
        )
    authenticator: ReplicaAuthenticator = ReplicaAuthenticator(
        sessions, replica, verifier, fingerprinter
    )
    audit: AuditLogService = AuditLogService(selected_clock, redactor, NOTIFICATION_AUDIT_MODELS)
    detector: Detector = build_detector(settings, env, http, settings.dlp_detectors)
    gate: DlpGate = DlpGate(detector, redactor, settings.dlp_policy)
    templates: TemplateService = TemplateService(
        TemplateRepository(), redactor, gate, audit, selected_clock
    )
    preference_repository: PreferenceRepository = ReplicaPreferenceRepository(replica)
    preferences: PreferenceService = PreferenceService(preference_repository, selected_clock)
    queue: NotificationTaskQueue = NotificationTaskQueue()
    service: NotificationService = NotificationService(
        gate,
        recipients,
        queue,
        audit,
        selected_clock,
        NotificationRepository(),
        DbTemplateResolver(TemplateRepository()),
        preference_repository,
        ReplicaMemberReferences(replica),
    )
    email_password: SecretValue | None = (
        resolve_secret(settings.smtp_password_ref, env)
        if settings.smtp_password_ref is not None
        else None
    )
    slack_token: SecretValue | None = (
        resolve_secret(settings.slack_bot_token_ref, env)
        if settings.slack_bot_token_ref is not None
        else None
    )
    email: EmailNotifier = EmailNotifier(
        SmtplibTransport(
            settings.smtp_host or "",
            settings.smtp_port,
            settings.smtp_username,
            email_password,
            settings.smtp_starttls,
        ),
        settings.smtp_from or "",
    )
    slack: SlackNotifier = SlackNotifier(http, slack_token)
    delivery: NotificationDeliveryHandler = NotificationDeliveryHandler(
        sessions, recipients, slack, email, audit, selected_clock, NotificationRepository()
    )
    pump: DeliveryPump = DeliveryPump(sessions, delivery, selected_clock)
    channels: ChannelStatus = ChannelStatus(
        settings.smtp_host is not None and settings.smtp_from is not None,
        slack_token is not None,
    )
    tester: NotificationServiceTester = NotificationServiceTester(
        sessions,
        service,
        delivery,
        ReplicaOwnerDirectory(replica),
        member_references=ReplicaMemberReferences(replica),
        channel_status=channels,
    )
    context: ApiContext = ApiContext(sessions, selected_clock, audit, authentication=authenticator)
    inbox: NotificationInbox = NotificationInbox(
        sessions,
        service,
        recipients,
        replica,
        selected_clock,
        settings.notification_replica_grace_seconds,
        ReplicaMemberReferences(replica),
    )
    return NotificationRuntime(
        sessions,
        engine,
        pump,
        inbox,
        context,
        templates,
        preferences,
        tester,
        inventory_secret,
        telescreen_secret,
        channels,
        replica,
    )


def create_notification_app(
    runtime: NotificationRuntime, http: httpx.AsyncClient | None = None
) -> FastAPI:
    """Serve internal service routes and existing public settings/template APIs."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        del app
        task: asyncio.Task[None] = asyncio.create_task(runtime.pump.run())
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

    app: FastAPI = FastAPI(title="Telescreen Notification", lifespan=lifespan)
    install_error_handlers(app)

    @app.middleware("http")
    async def service_auth(request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path.startswith("/api/"):
            return await call_next(request)
        supplied: str = request.headers.get(SERVICE_HEADER, "")
        telescreen_ok: bool = hmac.compare_digest(
            supplied.encode(), runtime.telescreen_secret.value.encode()
        )
        inventory_ok: bool = hmac.compare_digest(
            supplied.encode(), runtime.inventory_secret.value.encode()
        )
        command_path: str = NOTIFICATION_PREFIX + "/commands"
        if not telescreen_ok and not inventory_ok:
            return JSONResponse(
                {"error": {"code": "unauthorized", "detail": "service authentication required"}},
                status_code=401,
            )
        if (
            inventory_ok
            and not telescreen_ok
            and (request.url.path != command_path or request.method != "POST")
        ):
            return JSONResponse(
                {"error": {"code": "forbidden", "detail": "service operation is not permitted"}},
                status_code=403,
            )
        return await call_next(request)

    @app.post(NOTIFICATION_PREFIX + "/commands")
    async def receive_command(request: Request) -> Response:
        payload: str = await _read_payload(request)
        producer: Producer = (
            Producer.INVENTORY
            if hmac.compare_digest(
                request.headers.get(SERVICE_HEADER, "").encode(),
                runtime.inventory_secret.value.encode(),
            )
            else Producer.TELESCREEN
        )
        receipt: NotificationCommandReceipt = await runtime.inbox.receive(
            decode_notification_command(payload), producer
        )
        return Response(dump_json(json_str(receipt.value)), media_type="application/json")

    @app.post(NOTIFICATION_PREFIX + "/test")
    async def send_test(request: Request) -> Response:
        value: TestRequest = TypeAdapter(TestRequest).validate_json(
            await _read_payload(request), strict=True
        )
        result: TestOutcome = await runtime.tester.send_test(value.channel, value.member_id)
        return Response(
            dump_json(
                json_object(
                    (
                        ("ok", json_bool(result.ok)),
                        (
                            "error_code",
                            json_str(result.error_code.value)
                            if result.error_code is not None
                            else json_null(),
                        ),
                        (
                            "notification_id",
                            json_str(result.notification_id)
                            if result.notification_id is not None
                            else json_null(),
                        ),
                    )
                )
            ),
            media_type="application/json",
        )

    @app.get(NOTIFICATION_PREFIX + "/channels")
    async def channel_status() -> Response:
        return Response(
            dump_json(
                json_object(
                    (
                        ("email", json_bool(runtime.channels.email)),
                        ("slack", json_bool(runtime.channels.slack)),
                    )
                )
            ),
            media_type="application/json",
        )

    @app.get(NOTIFICATION_PREFIX + "/health")
    async def health() -> Response:
        async with runtime.sessions() as session:
            watermark: int = await runtime.replica.watermark(session)
        return Response(
            dump_json(json_object((("directory_revision", json_int(watermark)),))),
            media_type="application/json",
        )

    @app.post(NOTIFICATION_PREFIX + "/templates/lookup")
    async def template_lookup(request: Request) -> Response:
        payload: str = await _read_payload(request)
        name: str = TypeAdapter(str).validate_json(payload, strict=True)
        result: TemplateInfo | None
        async with runtime.sessions() as session:
            try:
                detail: TemplateDetail = await runtime.templates.get(session, name)
                result = TemplateInfo(detail.name, detail.event_kind, detail.active_version)
            except TelescreenError as error:
                if error.code is not ErrorCode.NOT_FOUND:
                    raise
                result = None
        return Response(
            TypeAdapter(TemplateInfo | None).dump_json(result), media_type="application/json"
        )

    app.include_router(
        build_notification_settings_router(
            NotificationSettingsRouterDeps(runtime.context, runtime.preferences)
        )
    )
    app.include_router(
        build_notification_templates_router(
            NotificationTemplatesRouterDeps(runtime.context, runtime.templates)
        )
    )
    return app
