"""Contract-1.1 exposure, claim liveness, and Police health behavior."""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import FakeClock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue
from telescreen.core.vocabulary import (
    DlpVerdict,
    ErrorCode,
    HealthCondition,
    HealthStatus,
    NotificationChannel,
    NotificationEvent,
    PoliceTaskStatus,
    PoliceWorkerStatus,
)
from telescreen.db.models import PoliceTaskRow, PoliceWorkerRow
from telescreen.masking.redact import Redactor
from telescreen.police.claims import ClaimRequest, ClaimService
from telescreen.police.health import (
    PoliceHealthProvider,
    WorkerHealth,
    list_worker_health,
    worker_health,
)
from telescreen.police.results import ResultService
from telescreen.ports.notifications import (
    NotificationReceipt,
    NotificationRequest,
)
from tests.police.fakes import FakeConfigs, FakeIssues, FakeMapSink, FakeSignaler, RecordingAudit
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import Sample, aws_access_key_id


@dataclass
class RecordingNotifications:
    """Capture safe notification requests sent by the health provider."""

    requests: list[NotificationRequest] = field(default_factory=list)

    async def send(
        self, session: AsyncSession, request: NotificationRequest
    ) -> NotificationReceipt:
        """Record the request without external delivery."""
        del session
        self.requests.append(request)
        return NotificationReceipt(("notification-id",), DlpVerdict.PASS, True)


def _result_service(issues: FakeIssues, audit: RecordingAudit, clock: FakeClock) -> ResultService:
    """Build a Police result service with offline ports."""
    return ResultService(
        issues,
        FakeConfigs(),
        FakeSignaler(),
        FakeMapSink(),
        Redactor.default(),
        audit,
        clock,
    )


def _task(task_type: str, now: datetime) -> PoliceTaskRow:
    """Build an open task row for result acceptance tests."""
    return PoliceTaskRow(
        id="task-id",
        task_type=task_type,
        labels="region=west",
        input_json="{}",
        status=PoliceTaskStatus.QUEUED.value,
        worker_id=None,
        attempt=0,
        max_attempts=3,
        deadline=now + timedelta(hours=1),
        created_at=now,
    )


def _worker(
    worker_id: str,
    now: datetime,
    *,
    status: str = PoliceWorkerStatus.ACTIVE.value,
    heartbeat: datetime | None = None,
    mode: str = "pull",
    labels: str = "region=west",
    last_delivery: datetime | None = None,
) -> PoliceWorkerRow:
    """Build a minimal registered worker row."""
    return PoliceWorkerRow(
        id=worker_id,
        name="worker",
        version="1.0",
        mode=mode,
        contract_version="1.1",
        task_types="detect,scan_shard",
        labels=labels,
        concurrency=2,
        status=status,
        last_heartbeat_at=heartbeat,
        last_delivery_at=last_delivery,
        created_at=now,
    )


@pytest.mark.asyncio
async def test_result_forwards_only_valid_resource_exposures() -> None:
    """Findings and complete resources accept exposure context; unknown locators drop."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 10, tzinfo=UTC)
    task: PoliceTaskRow = _task("detect", now)
    async with harness.sessions.begin() as session:
        session.add(task)
    issues: FakeIssues = FakeIssues()
    audit: RecordingAudit = RecordingAudit()
    body: bytes = json.dumps(
        {
            "contract_version": "1.1",
            "task_id": "task-id",
            "status": "completed",
            "findings": [
                {
                    "locator": "repo/a",
                    "rule_id": "credential",
                    "rule_class": "credential",
                    "masked_snippet": "[masked]",
                    "location": "file",
                    "confidence": 1,
                    "external_fingerprint": "fingerprint-000001",
                },
                {
                    "locator": "repo/b",
                    "rule_id": "credential",
                    "rule_class": "credential",
                    "masked_snippet": "[masked]",
                    "location": "file",
                    "confidence": 1,
                    "external_fingerprint": "fingerprint-000002",
                },
            ],
            "complete_locators": ["repo/clean"],
            "resource_exposures": [
                {
                    "locator": "repo/a",
                    "visibility": "public",
                    "external_principal_count": 2,
                    "shared_drive": True,
                    "repo_visibility": "public",
                    "is_fork": True,
                },
                {"locator": "repo/clean", "visibility": "private"},
                {"locator": "repo/unknown", "visibility": "domain"},
            ],
        }
    ).encode()
    service: ResultService = _result_service(issues, audit, FakeClock(now))
    async with harness.sessions.begin() as session:
        current: PoliceTaskRow | None = await session.get(PoliceTaskRow, "task-id")
        assert current is not None
        await service.accept(session, current, body)
    assert len(issues.scans) == 1
    assert len(issues.scans[0].findings) == 2
    assert len(issues.scans[0].exposures) == 2
    assert issues.scans[0].exposures[0].locator == "repo/a"
    assert issues.scans[0].exposures[0].exposure.external_principal_count == 2
    assert issues.scans[0].exposures[1].locator == "repo/clean"
    dropped: JsonValue | None = audit.entries[-1].details.get("exposures_dropped")
    assert dropped is not None and dropped.int_value == 1
    await harness.close()


@pytest.mark.asyncio
async def test_raw_value_in_exposure_locator_is_rejected_before_recording() -> None:
    """Exposure locators remain covered by the result redactor gate."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 10, tzinfo=UTC)
    task: PoliceTaskRow = _task("detect", now)
    async with harness.sessions.begin() as session:
        session.add(task)
    issues: FakeIssues = FakeIssues()
    audit: RecordingAudit = RecordingAudit()
    sample: Sample = aws_access_key_id()
    body: bytes = json.dumps(
        {
            "contract_version": "1.1",
            "task_id": "task-id",
            "status": "completed",
            "findings": [],
            "resource_exposures": [{"locator": "repo/" + sample.value, "visibility": "public"}],
        }
    ).encode()
    service: ResultService = _result_service(issues, audit, FakeClock(now))
    with pytest.raises(TelescreenError) as error:
        async with harness.sessions.begin() as session:
            current: PoliceTaskRow | None = await session.get(PoliceTaskRow, "task-id")
            assert current is not None
            await service.accept(session, current, body)
    assert error.value.code is ErrorCode.RAW_VALUE_DETECTED
    assert issues.scans == []
    assert sample.value not in repr(audit.entries)
    await harness.close()


@pytest.mark.asyncio
async def test_failed_scan_shard_keeps_finding_exposures() -> None:
    """Partial shard failure clears clean locators but preserves finding context."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 10, tzinfo=UTC)
    task: PoliceTaskRow = _task("scan_shard", now)
    issues: FakeIssues = FakeIssues()
    audit: RecordingAudit = RecordingAudit()
    body: bytes = json.dumps(
        {
            "contract_version": "1.1",
            "task_id": "task-id",
            "status": "completed",
            "findings": [
                {
                    "locator": "repo/finding",
                    "rule_id": "credential",
                    "rule_class": "credential",
                    "masked_snippet": "[masked]",
                    "location": "file",
                    "confidence": 1,
                    "external_fingerprint": "fingerprint-000001",
                }
            ],
            "stats": {
                "resources_scanned": 2,
                "resources_failed": 1,
                "bytes_scanned": 10,
            },
            "complete_locators": ["repo/clean"],
            "resource_exposures": [
                {"locator": "repo/finding", "visibility": "public"},
                {"locator": "repo/clean", "visibility": "private"},
            ],
        }
    ).encode()
    async with harness.sessions.begin() as session:
        session.add(task)
    service: ResultService = _result_service(issues, audit, FakeClock(now))
    async with harness.sessions.begin() as session:
        current: PoliceTaskRow | None = await session.get(PoliceTaskRow, "task-id")
        assert current is not None
        await service.accept(session, current, body)
    assert issues.scans[0].complete_locators == ()
    assert tuple(exposure.locator for exposure in issues.scans[0].exposures) == ("repo/finding",)
    dropped: JsonValue | None = audit.entries[-1].details.get("exposures_dropped")
    assert dropped is not None and dropped.int_value == 1
    await harness.close()


@pytest.mark.asyncio
async def test_contract_10_result_keeps_empty_exposures() -> None:
    """Legacy 1.0 result envelopes still record with the default empty exposures."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 10, tzinfo=UTC)
    task: PoliceTaskRow = _task("detect", now)
    async with harness.sessions.begin() as session:
        session.add(task)
    issues: FakeIssues = FakeIssues()
    service: ResultService = _result_service(issues, RecordingAudit(), FakeClock(now))
    async with harness.sessions.begin() as session:
        current: PoliceTaskRow | None = await session.get(PoliceTaskRow, "task-id")
        assert current is not None
        await service.accept(
            session,
            current,
            b'{"contract_version":"1.0","task_id":"task-id","status":"completed","findings":[]}',
        )
    assert len(issues.scans) == 1
    assert issues.scans[0].exposures == ()
    await harness.close()


@pytest.mark.asyncio
async def test_empty_claim_updates_worker_heartbeat() -> None:
    """An authenticated long-poll claim counts as liveness even with no task."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 10, tzinfo=UTC)
    stale: datetime = now - timedelta(minutes=10)
    worker: PoliceWorkerRow = _worker("worker-id", now, heartbeat=stale)
    async with harness.sessions.begin() as session:
        session.add(worker)
    claims: ClaimService = ClaimService(FakeClock(now))
    result: tuple[PoliceTaskRow, ...] = await claims.claim(
        harness.sessions, worker, ClaimRequest(1, 0)
    )
    assert result == ()
    async with harness.sessions() as session:
        stored: PoliceWorkerRow | None = await session.get(PoliceWorkerRow, "worker-id")
        assert stored is not None
        assert stored.last_heartbeat_at is not None
        assert ensure_utc(stored.last_heartbeat_at) == now
    await harness.close()


@pytest.mark.asyncio
async def test_worker_health_matches_task_types_and_labels() -> None:
    """Only old queued work matching worker capabilities opens police_silent."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 10, tzinfo=UTC)
    stale: datetime = now - timedelta(minutes=6)
    worker: PoliceWorkerRow = _worker("worker-id", now, heartbeat=stale)
    mismatched: PoliceTaskRow = _task("detect", stale)
    mismatched.labels = "region=east"
    disabled: PoliceWorkerRow = _worker("disabled", now, status=PoliceWorkerStatus.DISABLED.value)
    fresh: PoliceWorkerRow = _worker("fresh", now, heartbeat=now)
    async with harness.sessions.begin() as session:
        session.add_all((worker, mismatched, disabled, fresh))
    async with harness.sessions() as session:
        health: tuple[tuple[str, WorkerHealth], ...] = await list_worker_health(session, now)
    by_worker: tuple[WorkerHealth, ...] = tuple(
        item_health for worker_id, item_health in health if worker_id == "worker-id"
    )
    by_disabled: tuple[WorkerHealth, ...] = tuple(
        item_health for worker_id, item_health in health if worker_id == "disabled"
    )
    by_fresh: tuple[WorkerHealth, ...] = tuple(
        item_health for worker_id, item_health in health if worker_id == "fresh"
    )
    assert by_worker == (WorkerHealth(HealthStatus.WARNING),)
    assert by_disabled == (WorkerHealth(HealthStatus.UNKNOWN),)
    assert by_fresh == (WorkerHealth(HealthStatus.OK),)
    matching: WorkerHealth = worker_health(worker, stale, now)
    assert matching.status is HealthStatus.FAILING
    assert matching.conditions == (HealthCondition.POLICE_SILENT,)
    push: PoliceWorkerRow = _worker("push", now, mode="push", last_delivery=now)
    assert worker_health(push, None, now).status is HealthStatus.OK
    await harness.close()


@pytest.mark.asyncio
async def test_health_provider_alerts_once_and_clears_silently() -> None:
    """Police silence opens one two-channel alert and resets after liveness returns."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 10, tzinfo=UTC)
    stale: datetime = now - timedelta(minutes=6)
    worker: PoliceWorkerRow = _worker("worker-id", now, heartbeat=stale)
    task: PoliceTaskRow = _task("detect", stale)
    audit: RecordingAudit = RecordingAudit()
    notifications: RecordingNotifications = RecordingNotifications()
    clock: FakeClock = FakeClock(now)
    provider: PoliceHealthProvider = PoliceHealthProvider(
        audit,
        clock,
        notifications,
        ("team:security",),
    )
    async with harness.sessions.begin() as session:
        session.add_all((worker, task))
    async with harness.sessions.begin() as session:
        opened: int = await provider.tick(session, now)
    assert opened == 1
    assert len(notifications.requests) == 2
    assert tuple(request.channel for request in notifications.requests) == (
        NotificationChannel.EMAIL,
        NotificationChannel.SLACK,
    )
    assert all(
        request.event is NotificationEvent.POLICE_HEALTH_ALERT
        and request.context is not None
        and request.context.health_condition == "police_silent"
        for request in notifications.requests
    )
    assert len(audit.entries) == 1
    assert audit.entries[0].action == "health.alert_opened"
    async with harness.sessions.begin() as session:
        repeated: int = await provider.tick(session, now)
    assert repeated == 0
    assert len(notifications.requests) == 2
    later: datetime = now + timedelta(minutes=1)
    clock.set(later)
    async with harness.sessions.begin() as session:
        stored: PoliceWorkerRow | None = await session.get(PoliceWorkerRow, "worker-id")
        assert stored is not None
        stored.last_heartbeat_at = later
    async with harness.sessions.begin() as session:
        cleared: int = await provider.tick(session, later)
    assert cleared == 1
    async with harness.sessions() as session:
        stored = await session.get(PoliceWorkerRow, "worker-id")
        assert stored is not None
        assert stored.health_alerted_at is None
    assert len(notifications.requests) == 2
    assert len(audit.entries) == 1
    await harness.close()
