"""Inventory reconciliation from successful map reductions."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import FakeClock, ensure_utc
from telescreen.core.json import dump_json
from telescreen.core.vocabulary import MapItemStatus, MapJobStatus, MapOrigin
from telescreen.db.models import MapItemRow, MapJobRow
from telescreen.workflow.mapreduce.reduce import MapReduceHandler
from telescreen.workflow.mapreduce.service import spec_to_json
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.workflow.mapreduce.test_mapreduce import (
    AuditFake,
    GatewayFake,
    IssueFake,
    QueueFake,
    SignalFake,
    _spec,
)


@dataclass
class RecordingReconciler:
    """Capture sweep reconciliation arguments."""

    calls: list[tuple[str, datetime]]

    async def reconcile_sweep(
        self, session: AsyncSession, collector_config_id: str, seen_before: datetime
    ) -> int:
        """Record a successful sweep reconciliation."""
        del session
        self.calls.append((collector_config_id, seen_before))
        return 0

    async def reconcile_listing(
        self, session: AsyncSession, collector_config_id: str, listed_keys: frozenset[str]
    ) -> int:
        """Ignore listing reconciliation in this sweep test."""
        del session, collector_config_id, listed_keys
        return 0

    async def stop_tracking(
        self, session: AsyncSession, collector_config_id: str, actor_ref: str
    ) -> int:
        """Ignore tracking mode changes in this sweep test."""
        del session, collector_config_id, actor_ref
        return 0


@pytest.mark.asyncio
async def test_succeeded_map_reconciles_inventory_at_job_watermark() -> None:
    """A successful map job calls inventory reconciliation in the reduce transaction."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
    reconciler: RecordingReconciler = RecordingReconciler([])
    try:
        async with harness.sessions.begin() as session:
            session.add(
                MapJobRow(
                    id="inventory-job",
                    origin=MapOrigin.COLLECTOR.value,
                    step_run_id=None,
                    collector_config_id="collector-1",
                    spec_json=dump_json(spec_to_json(_spec())),
                    status=MapJobStatus.RUNNING.value,
                    partition_done=True,
                    max_concurrency=32,
                    paused_until=None,
                    deadline=now + timedelta(days=1),
                    result_json=None,
                    created_at=now,
                    finished_at=None,
                )
            )
        reducer: MapReduceHandler = MapReduceHandler(
            harness.sessions,
            QueueFake(set(), []),
            GatewayFake(),
            IssueFake([]),
            SignalFake(),
            AuditFake(),
            FakeClock(now),
            inventory=reconciler,
        )
        await reducer._handle_id("inventory-job")
        assert reconciler.calls[0][0] == "collector-1"
        assert ensure_utc(reconciler.calls[0][1]) == now
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_failed_map_does_not_reconcile_inventory() -> None:
    """An overdue map job cannot infer deletion from an incomplete scan."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
    reconciler: RecordingReconciler = RecordingReconciler([])
    try:
        async with harness.sessions.begin() as session:
            session.add(
                MapJobRow(
                    id="failed-inventory-job",
                    origin=MapOrigin.COLLECTOR.value,
                    step_run_id=None,
                    collector_config_id="collector-1",
                    spec_json=dump_json(spec_to_json(_spec())),
                    status=MapJobStatus.RUNNING.value,
                    partition_done=True,
                    max_concurrency=32,
                    paused_until=None,
                    deadline=now - timedelta(seconds=1),
                    result_json=None,
                    created_at=now - timedelta(minutes=1),
                    finished_at=None,
                )
            )
        reducer: MapReduceHandler = MapReduceHandler(
            harness.sessions,
            QueueFake(set(), []),
            GatewayFake(),
            IssueFake([]),
            SignalFake(),
            AuditFake(),
            FakeClock(now),
            inventory=reconciler,
        )
        await reducer._handle_id("failed-inventory-job")
        async with harness.sessions() as session:
            job: MapJobRow | None = await session.get(MapJobRow, "failed-inventory-job")
        assert job is not None and job.status == MapJobStatus.FAILED.value
        assert reconciler.calls == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_partial_map_does_not_reconcile_inventory() -> None:
    """A partial map job cannot infer deletion from failed resources."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 9, tzinfo=UTC)
    reconciler: RecordingReconciler = RecordingReconciler([])
    try:
        async with harness.sessions.begin() as session:
            session.add(
                MapJobRow(
                    id="partial-inventory-job",
                    origin=MapOrigin.COLLECTOR.value,
                    step_run_id=None,
                    collector_config_id="collector-1",
                    spec_json=dump_json(spec_to_json(_spec())),
                    status=MapJobStatus.RUNNING.value,
                    partition_done=True,
                    max_concurrency=32,
                    paused_until=None,
                    deadline=now + timedelta(days=1),
                    result_json=None,
                    created_at=now,
                    finished_at=None,
                )
            )
            session.add(
                MapItemRow(
                    id="partial-inventory-item",
                    map_job_id="partial-inventory-job",
                    shard_key="shard",
                    descriptor_json='{"id":"opaque"}',
                    weight=1.0,
                    status=MapItemStatus.SUCCEEDED.value,
                    attempt=1,
                    dispatched_at=None,
                    finished_at=now,
                    summary_json='{"resources_failed":1,"resources_scanned":0}',
                    error_code=None,
                    police_task_id=None,
                    scan_job_id="scan-partial",
                )
            )
        reducer: MapReduceHandler = MapReduceHandler(
            harness.sessions,
            QueueFake(set(), []),
            GatewayFake(),
            IssueFake([]),
            SignalFake(),
            AuditFake(),
            FakeClock(now),
            inventory=reconciler,
        )
        await reducer._handle_id("partial-inventory-job")
        async with harness.sessions() as session:
            job: MapJobRow | None = await session.get(MapJobRow, "partial-inventory-job")
        assert job is not None and job.status == MapJobStatus.PARTIAL.value
        assert reconciler.calls == []
    finally:
        await harness.close()
