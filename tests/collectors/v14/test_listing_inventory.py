"""Full-listing inventory reconciliation safety."""

from dataclasses import dataclass, replace
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.collectors.registry import CollectorRegistry
from telescreen.collectors.repository import CollectorRepository, ScanJobStoreImpl
from telescreen.collectors.tasks import CollectorScanHandler
from telescreen.core.vocabulary import CollectorKind
from telescreen.ports.collectors import ChangePage, CollectorConfig, ResourceRef
from telescreen.ports.workflow import TaskResult, TaskResultKind
from tests.collectors.core.test_runtime import (
    NOW,
    RAW_EMAIL,
    RuntimeAdapter,
    RuntimePorts,
    RuntimeScanner,
    _create_config,
    _scan_handler,
    _scan_task,
)
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass
class RecordingReconciler:
    """Record authoritative listing reconciliations."""

    calls: list[tuple[str, frozenset[str]]]

    async def reconcile_sweep(
        self, session: AsyncSession, collector_config_id: str, seen_before: datetime
    ) -> int:
        """Ignore sweep calls in this full-listing test."""
        del session, collector_config_id, seen_before
        return 0

    async def reconcile_listing(
        self, session: AsyncSession, collector_config_id: str, listed_keys: frozenset[str]
    ) -> int:
        """Capture the accepted complete listing."""
        del session
        self.calls.append((collector_config_id, listed_keys))
        return 0

    async def stop_tracking(
        self, session: AsyncSession, collector_config_id: str, actor_ref: str
    ) -> int:
        """Ignore tracking mode changes in this test."""
        del session, collector_config_id, actor_ref
        return 0


@pytest.mark.asyncio
async def test_full_listing_reconciles_only_when_no_reference_was_skipped() -> None:
    """Safe full listings reconcile; a redactor-skipped ref suppresses deletion inference."""
    harness: SqliteHarness = await make_sqlite_harness()
    repo: CollectorRepository = CollectorRepository()
    try:
        ports: RuntimePorts = RuntimePorts()
        safe_ref: ResourceRef = ResourceRef(CollectorKind.SLACK, '{"id":"listed"}', "listed")
        skipped_ref: ResourceRef = ResourceRef(CollectorKind.SLACK, RAW_EMAIL, "safe-locator")
        adapter: RuntimeAdapter = RuntimeAdapter(
            pages=(ChangePage((safe_ref,), (), None, True, False),)
        )
        registry: CollectorRegistry = CollectorRegistry()
        registry.register(adapter)
        scanner: RuntimeScanner = RuntimeScanner(ports)
        config: CollectorConfig = await _create_config(harness, repo, NOW)
        handler: CollectorScanHandler = _scan_handler(
            harness,
            registry,
            scanner,
            ports,
            ScanJobStoreImpl(repo),
            repo,
        )
        reconciler: RecordingReconciler = RecordingReconciler([])
        handler = replace(handler, inventory=reconciler)
        await handler.handle(_scan_task(config.id, "changes", None))
        assert len(reconciler.calls) == 1
        assert len(reconciler.calls[0][1]) == 1

        adapter.pages = (ChangePage((skipped_ref,), (), None, True, False),)
        adapter.list_calls = 0
        await handler.handle(_scan_task(config.id, "changes", None))
        assert len(reconciler.calls) == 1
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_incremental_listing_does_not_reconcile_inventory() -> None:
    """An incremental page does not claim resources absent from the page are deleted."""
    harness: SqliteHarness = await make_sqlite_harness()
    repo: CollectorRepository = CollectorRepository()
    try:
        ports: RuntimePorts = RuntimePorts()
        safe_ref: ResourceRef = ResourceRef(CollectorKind.SLACK, '{"id":"listed"}', "listed")
        adapter: RuntimeAdapter = RuntimeAdapter(
            pages=(ChangePage((safe_ref,), (), None, False, False),)
        )
        registry: CollectorRegistry = CollectorRegistry()
        registry.register(adapter)
        scanner: RuntimeScanner = RuntimeScanner(ports)
        config: CollectorConfig = await _create_config(harness, repo, NOW)
        handler: CollectorScanHandler = _scan_handler(
            harness,
            registry,
            scanner,
            ports,
            ScanJobStoreImpl(repo),
            repo,
        )
        reconciler: RecordingReconciler = RecordingReconciler([])
        handler = replace(handler, inventory=reconciler)
        await handler.handle(_scan_task(config.id, "changes", None))
        assert reconciler.calls == []
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_listing_fetch_exception_does_not_reconcile_inventory() -> None:
    """A failed full listing returns retry without inferring deletion."""
    harness: SqliteHarness = await make_sqlite_harness()
    repo: CollectorRepository = CollectorRepository()
    try:
        ports: RuntimePorts = RuntimePorts()
        safe_ref: ResourceRef = ResourceRef(CollectorKind.SLACK, '{"id":"listed"}', "listed")
        adapter: RuntimeAdapter = RuntimeAdapter(
            pages=(ChangePage((safe_ref,), (), None, True, False),), fetch_error=True
        )
        registry: CollectorRegistry = CollectorRegistry()
        registry.register(adapter)
        scanner: RuntimeScanner = RuntimeScanner(ports)
        config: CollectorConfig = await _create_config(harness, repo, NOW)
        handler: CollectorScanHandler = _scan_handler(
            harness,
            registry,
            scanner,
            ports,
            ScanJobStoreImpl(repo),
            repo,
        )
        reconciler: RecordingReconciler = RecordingReconciler([])
        handler = replace(handler, inventory=reconciler)
        result: TaskResult = await handler.handle(_scan_task(config.id, "changes", None))
        assert result.kind is TaskResultKind.RETRY
        assert reconciler.calls == []
    finally:
        await harness.close()
