"""Tests for the source-agnostic collector core."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Headers
from httpx import Response as HttpxResponse
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.common import ApiContext, install_error_handlers
from telescreen.api.routers.collectors import (
    CollectorsRouterDeps,
    WebhookSecrets,
    build_collectors_router,
)
from telescreen.collectors.codec import (
    CursorState,
    cursor_from_json,
    cursor_to_json,
    selector_from_json,
    selector_to_json,
)
from telescreen.collectors.police import PoliceCollector
from telescreen.collectors.registry import CollectorRegistry
from telescreen.collectors.repository import CollectorRepository, ScanJobStoreImpl
from telescreen.collectors.scheduler import CollectorScheduleProvider
from telescreen.collectors.service import CollectorService, WebhookOutcome
from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, dump_json, json_object, parse_json
from telescreen.core.vocabulary import (
    ApiScope,
    CollectorKind,
    ErrorCode,
    ScanJobStatus,
    ScanTrigger,
    TriggerMode,
)
from telescreen.directory import CreatedToken, TokenService
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry
from telescreen.ports.collectors import (
    ChangePage,
    CollectorConfig,
    CollectorCursor,
    CollectorSecrets,
    CustomSelector,
    EventCheck,
    EventCheckKind,
    EventResources,
    InboundWebhook,
    ResourceContent,
    ResourceRef,
    SlackSelector,
    WatchState,
)
from telescreen.ports.police import PoliceTaskRequest
from telescreen.ports.workflow import TaskSpec
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import assert_no_leaks, email_address

NOW: datetime = datetime(2026, 10, 9, 12, tzinfo=UTC)


@dataclass
class FakePorts:
    """Fake audit sink, task queue, and event adapter."""

    tasks: list[TaskSpec] = field(default_factory=list)
    audits: list[AuditEntry] = field(default_factory=list)
    serialized_task_payloads: list[str] = field(default_factory=list)
    serialized_audit_details: list[str] = field(default_factory=list)
    kind: CollectorKind = CollectorKind.SLACK
    event_check: EventCheck = EventCheck(EventCheckKind.VALID)
    event_resources: EventResources = EventResources((), ())
    resource: ResourceRef = ResourceRef(
        CollectorKind.SLACK, '{"id":"r1"}', "slack:t/c/" + email_address().value
    )
    sweep_conflict: bool = False

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Capture safe audit entries."""
        self.audits.append(entry)
        self.serialized_audit_details.append(dump_json(entry.details))
        return len(self.audits)

    async def enqueue(self, session: AsyncSession, spec: TaskSpec) -> bool:
        """Capture task specs and emulate idempotency."""
        if any(item.idempotency_key == spec.idempotency_key for item in self.tasks):
            return False
        self.tasks.append(spec)
        self.serialized_task_payloads.append(dump_json(spec.payload))
        return True

    async def cancel_by_ref(self, session: AsyncSession, ref_id: str) -> int:
        """No-op unused queue method."""
        return 0

    async def dispatch(self, session: AsyncSession, request: PoliceTaskRequest) -> str:
        """Return a stable fake Police task id."""
        return "police-task-test"

    async def cancel(self, session: AsyncSession, task_id: str) -> None:
        """No-op Police cancellation for unrelated scheduler tests."""
        return None

    async def verify_event(self, event: InboundWebhook, secrets: CollectorSecrets) -> EventCheck:
        """Accept test webhook events."""
        return self.event_check

    async def resources_from_event(
        self, event: InboundWebhook, config: CollectorConfig
    ) -> EventResources:
        """Return one event resource."""
        return (
            self.event_resources
            if self.event_resources != EventResources((), ())
            else EventResources((self.resource,), ())
        )

    async def list_changed(
        self, config: CollectorConfig, cursor: CollectorCursor | None
    ) -> ChangePage:
        """Return an empty page for unrelated adapter operations."""
        return ChangePage((), (), None, False, False)

    async def fetch(self, config: CollectorConfig, ref: ResourceRef) -> ResourceContent:
        """Return no content for unrelated adapter operations."""
        return ResourceContent(
            config.kind.value, config.id, (), True, True, ref.raw_locator, "title", ref
        )

    async def renew_watch(
        self, config: CollectorConfig, current: WatchState | None
    ) -> WatchState | None:
        """Leave watches disabled in this fake."""
        return None

    def ref_from_json(self, ref_json: str) -> ResourceRef:
        """Decode a test reference."""
        return self.resource

    async def start_collector_sweep(self, collector_config_id: str, trigger: ScanTrigger) -> str:
        """Return a stable map job id or emulate an active-sweep conflict."""
        if self.sweep_conflict:
            raise TelescreenError(ErrorCode.CONFLICT, "sweep already running")
        return "map-job-test"


@pytest.mark.asyncio
async def test_selector_and_cursor_json_round_trip() -> None:
    """Encode typed selectors and UTC cursor/watch state without losing values."""
    selectors: tuple[SlackSelector, ...] = (SlackSelector(("C123",), True),)
    for selector in selectors:
        encoded: JsonValue = selector_to_json(selector)
        assert selector_from_json(CollectorKind.SLACK, encoded) == selector
    expires: datetime = datetime(2026, 10, 10, 12, tzinfo=UTC)
    state: CursorState = CursorState("page-2", WatchState("channel", "resource", expires), expires)
    assert cursor_from_json(cursor_to_json(state)) == state
    with pytest.raises(TelescreenError) as error:
        selector_from_json(
            CollectorKind.SLACK, parse_json('{"channel_ids":4,"include_files":true}')
        )
    assert error.value.code is ErrorCode.VALIDATION_ERROR


@pytest.mark.asyncio
async def test_collector_repository_round_trip_and_scan_job() -> None:
    """Persist a config and scan job using the shared SQLite harness."""
    harness: SqliteHarness = await make_sqlite_harness()
    repository: CollectorRepository = CollectorRepository()
    try:
        async with harness.sessions.begin() as session:
            config: CollectorConfig = await repository.create(
                session,
                "cfg-1",
                "daily-scan",
                CollectorKind.SLACK,
                TriggerMode.EVENT,
                None,
                None,
                16,
                SlackSelector(("C123",), False),
                "env:SLACK_TOKEN",
                NOW,
            )
            scan_id: str = await repository.create_scan_job(
                session, config.id, "slack", ScanTrigger.EVENT, NOW
            )
        async with harness.sessions() as session:
            loaded: CollectorConfig | None = await repository.get(session, config.id)
            assert loaded is not None and loaded.name == "daily-scan"
            assert loaded.selector == SlackSelector(("C123",), False)
        async with harness.sessions.begin() as session:
            await repository.finish_scan_job(
                session, scan_id, ScanJobStatus.SUCCEEDED, 1, 0, 2, None, NOW
            )
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_service_rejects_invalid_config_values() -> None:
    """Reject malformed schedules, secret literals, and raw selector values."""
    fake: FakePorts = FakePorts()
    service: CollectorService = _service(fake, CollectorRepository())
    with pytest.raises(TelescreenError) as cron_error:
        service._validate(
            "daily-scan",
            CollectorKind.SLACK,
            TriggerMode.SCHEDULE,
            "bad",
            None,
            SlackSelector(("C1",), False),
            None,
        )
    assert cron_error.value.code is ErrorCode.VALIDATION_ERROR
    with pytest.raises(TelescreenError) as credential_error:
        service._validate(
            "daily-scan",
            CollectorKind.SLACK,
            TriggerMode.EVENT,
            None,
            None,
            SlackSelector(("C1",), False),
            "raw-secret",
        )
    assert credential_error.value.code is ErrorCode.VALIDATION_ERROR
    with pytest.raises(TelescreenError) as raw_error:
        service._validate(
            "daily-scan",
            CollectorKind.CUSTOM,
            TriggerMode.EVENT,
            None,
            None,
            _custom_selector(),
            None,
        )
    assert raw_error.value.code is ErrorCode.RAW_VALUE_DETECTED


@pytest.mark.asyncio
async def test_webhook_invalid_challenge_and_poll_changes_modes() -> None:
    """Invalid signatures stop early, challenges echo safely, and polling queues work."""
    harness: SqliteHarness = await make_sqlite_harness()
    fake: FakePorts = FakePorts()
    repository: CollectorRepository = CollectorRepository()
    registry: CollectorRegistry = CollectorRegistry()
    registry.register(fake)
    service: CollectorService = _service(fake, repository, registry)
    event: InboundWebhook = InboundWebhook(CollectorKind.SLACK, "POST", NOW, (), b"safe event")
    try:
        fake.event_check = EventCheck(EventCheckKind.INVALID)
        async with harness.sessions.begin() as session:
            invalid: WebhookOutcome = await service.handle_webhook(
                session, CollectorKind.SLACK, event, CollectorSecrets(None, None)
            )
        assert invalid.status == 401
        assert fake.tasks == [] and fake.audits == []

        fake.event_check = EventCheck(EventCheckKind.CHALLENGE, "challenge-token")
        async with harness.sessions.begin() as session:
            challenge: WebhookOutcome = await service.handle_webhook(
                session, CollectorKind.SLACK, event, CollectorSecrets(None, None)
            )
        assert challenge.status == 200 and challenge.body == "challenge-token"
        assert fake.tasks == []

        async with harness.sessions.begin() as session:
            config: CollectorConfig = await service.create(
                session,
                "slack-prod",
                CollectorKind.SLACK,
                TriggerMode.EVENT,
                None,
                None,
                SlackSelector(("C123",), False),
                None,
                "member:1",
            )
        fake.event_check = EventCheck(EventCheckKind.VALID)
        fake.event_resources = EventResources((), (), True)
        async with harness.sessions.begin() as session:
            accepted: WebhookOutcome = await service.handle_webhook(
                session, CollectorKind.SLACK, event, CollectorSecrets(None, None)
            )
        assert accepted.status == 200
        assert len(fake.tasks) == 1
        assert fake.tasks[0].ref_id == config.id
        mode_value: JsonValue | None = fake.tasks[0].payload.get("mode")
        assert mode_value is not None
        assert mode_value.as_str() == "changes"
        assert_no_leaks(dump_json(fake.tasks[0].payload), "poll changes task payload")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_webhook_enqueues_hmac_keys_without_raw_locator_and_deduplicates() -> None:
    """Route webhook refs into idempotent tasks without persisting raw locators."""
    harness: SqliteHarness = await make_sqlite_harness()
    fake: FakePorts = FakePorts()
    repository: CollectorRepository = CollectorRepository()
    registry: CollectorRegistry = CollectorRegistry()
    registry.register(fake)
    service: CollectorService = _service(fake, repository, registry)
    try:
        async with harness.sessions.begin() as session:
            config: CollectorConfig = await service.create(
                session,
                "slack-prod",
                CollectorKind.SLACK,
                TriggerMode.EVENT,
                None,
                None,
                SlackSelector(("C123",), False),
                "env:SLACK_TOKEN",
                "member:1",
            )
        fake.event_resources = EventResources(
            (fake.resource,),
            (
                ResourceRef(
                    CollectorKind.SLACK,
                    '{"id":"removed"}',
                    "slack:file/" + email_address().value,
                ),
            ),
        )
        event: InboundWebhook = InboundWebhook(CollectorKind.SLACK, "POST", NOW, (), b"safe event")
        async with harness.sessions.begin() as session:
            outcome: WebhookOutcome = await service.handle_webhook(
                session, CollectorKind.SLACK, event, CollectorSecrets(None, None)
            )
        async with harness.sessions.begin() as session:
            await service.handle_webhook(
                session, CollectorKind.SLACK, event, CollectorSecrets(None, None)
            )
        assert outcome.status == 200
        assert len(fake.tasks) == 2
        async with harness.sessions.begin() as session:
            assert await service.enqueue_run(session, config.id, ScanTrigger.MANUAL)
        async with harness.sessions.begin() as session:
            scheduled: CollectorConfig = await service.create(
                session,
                "slack-scheduled",
                CollectorKind.SLACK,
                TriggerMode.SCHEDULE,
                "0 12 * * *",
                None,
                SlackSelector(("C456",), False),
                None,
                "member:1",
            )
        provider: CollectorScheduleProvider = CollectorScheduleProvider(
            fake, fake, PoliceCollector(fake, FakeClock(NOW)), repository, FakeClock(NOW)
        )
        async with harness.sessions.begin() as session:
            assert await provider.tick(session, NOW) == 1
        assert len(fake.tasks) == 4
        assert_no_leaks(dump_json(fake.tasks[0].payload), "collector task payload")
        assert_no_leaks(" ".join(fake.serialized_audit_details), "serialized audit details")
        assert_no_leaks(" ".join(fake.serialized_task_payloads), "serialized task payloads")
        assert '"trigger":"event"' in dump_json(fake.tasks[0].payload)
        assert '"trigger":"manual"' in dump_json(fake.tasks[2].payload)
        assert '"trigger":"schedule"' in dump_json(fake.tasks[-1].payload)
        assert fake.tasks[-1].ref_id == scheduled.id
        assert fake.tasks[0].idempotency_key.startswith(f"collector:{config.id}:")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_webhook_config_hint_selects_matching_watch() -> None:
    """Route a channel hint to only the collector config with that watch."""
    harness: SqliteHarness = await make_sqlite_harness()
    fake: FakePorts = FakePorts()
    repository: CollectorRepository = CollectorRepository()
    registry: CollectorRegistry = CollectorRegistry()
    registry.register(fake)
    service: CollectorService = _service(fake, repository, registry)
    try:
        async with harness.sessions.begin() as session:
            first: CollectorConfig = await service.create(
                session,
                "slack-first",
                CollectorKind.SLACK,
                TriggerMode.EVENT,
                None,
                None,
                SlackSelector(("C1",), False),
                None,
                "member:1",
            )
            second: CollectorConfig = await service.create(
                session,
                "slack-second",
                CollectorKind.SLACK,
                TriggerMode.EVENT,
                None,
                None,
                SlackSelector(("C2",), False),
                None,
                "member:1",
            )
            await repository.set_cursor(
                session, first.id, CursorState(None, WatchState("channel-a", "r1", NOW), None), NOW
            )
            await repository.set_cursor(
                session, second.id, CursorState(None, WatchState("channel-b", "r2", NOW), None), NOW
            )
        fake.event_check = EventCheck(EventCheckKind.VALID, config_hint="channel-b")
        event: InboundWebhook = InboundWebhook(CollectorKind.SLACK, "POST", NOW, (), b"event")
        async with harness.sessions.begin() as session:
            await service.handle_webhook(
                session, CollectorKind.SLACK, event, CollectorSecrets(None, None)
            )
        assert len(fake.tasks) == 1
        assert fake.tasks[0].ref_id == second.id
        assert fake.tasks[0].ref_id != first.id
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_collectors_router_responses_conflicts_and_no_echo() -> None:
    """Return map_job_id, map conflicts to 409, and hide invalid config values."""
    harness: SqliteHarness = await make_sqlite_harness()
    fake: FakePorts = FakePorts()
    repository: CollectorRepository = CollectorRepository()
    registry: CollectorRegistry = CollectorRegistry()
    registry.register(fake)
    service: CollectorService = _service(fake, repository, registry)
    clock: FakeClock = FakeClock(NOW)
    context: ApiContext = ApiContext(harness.sessions, clock, fake)
    tokens: TokenService = TokenService(fake, clock)
    try:
        async with harness.sessions.begin() as session:
            config: CollectorConfig = await service.create(
                session,
                "slack-prod",
                CollectorKind.SLACK,
                TriggerMode.EVENT,
                None,
                None,
                SlackSelector(("C123",), False),
                None,
                "member:1",
            )
            token: CreatedToken = await tokens.create(
                session, "test-admin", frozenset((ApiScope.ADMIN,)), None
            )
        app: FastAPI = FastAPI()
        install_error_handlers(app)
        app.include_router(
            build_collectors_router(
                CollectorsRouterDeps(
                    context,
                    service,
                    fake,
                    WebhookSecrets(
                        CollectorSecrets(None, None),
                        CollectorSecrets(None, None),
                        CollectorSecrets(None, None),
                    ),
                )
            )
        )
        headers: Headers = Headers({"Authorization": f"Bearer {token.token}"})
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            swept: HttpxResponse = await client.post(
                f"/api/collectors/{config.id}/sweep", headers=headers
            )
            assert swept.status_code == 200
            assert swept.json() == {"map_job_id": "map-job-test"}
            fake.sweep_conflict = True
            conflict: HttpxResponse = await client.post(
                f"/api/collectors/{config.id}/sweep", headers=headers
            )
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == ErrorCode.CONFLICT.value
            bad_cron: HttpxResponse = await client.post(
                "/api/collectors",
                headers=headers,
                json={
                    "name": "bad-cron",
                    "kind": "slack",
                    "trigger_mode": "schedule",
                    "cron": "bad",
                    "sweep_cron": None,
                    "selector_json": '{"channel_ids":["C1"],"include_files":false}',
                    "credential_ref": None,
                },
            )
            assert bad_cron.status_code == 422 and "bad" not in bad_cron.text
            bad_credential: HttpxResponse = await client.post(
                "/api/collectors",
                headers=headers,
                json={
                    "name": "bad-credential",
                    "kind": "slack",
                    "trigger_mode": "event",
                    "cron": None,
                    "sweep_cron": None,
                    "selector_json": '{"channel_ids":["C1"],"include_files":false}',
                    "credential_ref": "raw-secret",
                },
            )
            assert bad_credential.status_code == 422
            assert "raw-secret" not in bad_credential.text
            raw_selector: HttpxResponse = await client.post(
                "/api/collectors",
                headers=headers,
                json={
                    "name": "bad-selector",
                    "kind": "slack",
                    "trigger_mode": "event",
                    "cron": None,
                    "sweep_cron": None,
                    "selector_json": (
                        '{"channel_ids":["person@example.invalid"],"include_files":false}'
                    ),
                    "credential_ref": None,
                },
            )
            assert raw_selector.status_code == 422
            assert "person@example.invalid" not in raw_selector.text
    finally:
        await harness.close()


def _service(
    fake: FakePorts, repository: CollectorRepository, registry: CollectorRegistry | None = None
) -> CollectorService:
    """Build a service around deterministic local fakes."""
    key: bytes = b"k" * 32
    return CollectorService(
        registry or CollectorRegistry(),
        fake,
        ScanJobStoreImpl(repository),
        repository,
        Fingerprinter(key, "test"),
        Redactor.default(),
        fake,
        FakeClock(NOW),
    )


def _custom_selector() -> CustomSelector:
    """Return a custom selector containing a detected sample email."""
    from telescreen.core.json import json_str

    return CustomSelector(json_object((("owner", json_str(email_address().value)),)))
