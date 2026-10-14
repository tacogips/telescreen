"""Police result inventory locator filtering and audit details."""

from datetime import UTC, datetime, timedelta

import pytest

from telescreen.core.clock import FakeClock
from telescreen.core.json import JsonValue, dump_json, parse_json
from telescreen.core.vocabulary import PoliceTaskStatus
from telescreen.db.models import PoliceTaskRow
from telescreen.masking.redact import Redactor
from telescreen.police.results import ResultService, _resource_inventories
from telescreen.ports.findings import LocatorInventory
from tests.police.fakes import FakeConfigs, FakeIssues, FakeMapSink, FakeSignaler, RecordingAudit
from tests.support.db import SqliteHarness, make_sqlite_harness


def test_inventory_parser_drops_locators_absent_from_findings_and_completion() -> None:
    """Only inventories tied to a completed or finding-bearing locator survive."""
    payload: JsonValue = parse_json(
        '{"resource_inventories":['
        '{"locator":"allowed","item_fingerprints":["' + "a" * 64 + '"]},'
        '{"locator":"not-allowed","item_count":2}]}'
    )
    entries: tuple[LocatorInventory, ...]
    dropped: int
    entries, dropped = _resource_inventories(
        payload.get("resource_inventories"), frozenset(("allowed",))
    )
    assert len(entries) == 1
    assert entries[0].locator == "allowed"
    assert entries[0].item_fingerprints == ("a" * 64,)
    assert dropped == 1


@pytest.mark.asyncio
async def test_detect_result_routes_allowed_inventory_and_audits_drops() -> None:
    """Police 1.2 detect inventory reaches issues and dropped locators are audited."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    task: PoliceTaskRow = PoliceTaskRow(
        id="task-inventory",
        task_type="detect",
        labels="",
        input_json="{}",
        status=PoliceTaskStatus.QUEUED.value,
        worker_id=None,
        attempt=0,
        max_attempts=3,
        deadline=now + timedelta(hours=1),
        created_at=now,
    )
    issues: FakeIssues = FakeIssues()
    audit: RecordingAudit = RecordingAudit()
    service: ResultService = ResultService(
        issues,
        FakeConfigs(),
        FakeSignaler(),
        FakeMapSink(),
        Redactor.default(),
        audit,
        FakeClock(now),
    )
    result: bytes = (
        b'{"contract_version":"1.2","task_id":"task-inventory",'
        b'"status":"completed","outcome":"passed","findings":[],"complete_locators":["allowed"],"'
        b'resource_inventories":['
        b'{"locator":"allowed","item_count":3},'
        b'{"locator":"not-allowed","item_count":7}]}'
    )
    try:
        async with harness.sessions.begin() as session:
            await service.accept(session, task, result)
        assert len(issues.scans) == 1
        assert tuple(item.locator for item in issues.scans[0].inventories) == ("allowed",)
        assert '"inventories_dropped":1' in dump_json(audit.entries[0].details)
    finally:
        await harness.close()
