"""Collector inventory mode defaults, persistence, and stop transition."""

from dataclasses import dataclass, replace
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.collectors.repository import CollectorRepository
from telescreen.collectors.service import CollectorService
from telescreen.core.json import dump_json
from telescreen.core.vocabulary import CollectorKind, InventoryMode, TriggerMode
from telescreen.ports.collectors import CollectorConfig, SlackSelector
from tests.collectors.core.test_core import FakePorts, _service
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass
class RecordingReconciler:
    """Capture inventory stop requests from collector updates."""

    stopped: list[tuple[str, str]]

    async def reconcile_sweep(
        self, session: AsyncSession, collector_config_id: str, seen_before: datetime
    ) -> int:
        """Do not reconcile sweeps in this collector service test."""
        del session, collector_config_id, seen_before
        return 0

    async def reconcile_listing(
        self, session: AsyncSession, collector_config_id: str, listed_keys: frozenset[str]
    ) -> int:
        """Do not reconcile listings in this collector service test."""
        del session, collector_config_id, listed_keys
        return 0

    async def stop_tracking(
        self, session: AsyncSession, collector_config_id: str, actor_ref: str
    ) -> int:
        """Record the single tracking stop transition."""
        del session
        self.stopped.append((collector_config_id, actor_ref))
        return 0


@pytest.mark.asyncio
async def test_inventory_mode_defaults_and_off_transition_are_persisted() -> None:
    """Omitted updates preserve mode, and switching off stops tracking once."""
    harness: SqliteHarness = await make_sqlite_harness()
    ports: FakePorts = FakePorts()
    repository: CollectorRepository = CollectorRepository()
    reconciler: RecordingReconciler = RecordingReconciler([])
    service: CollectorService = replace(_service(ports, repository), inventory=reconciler)
    try:
        async with harness.sessions.begin() as session:
            created: CollectorConfig = await service.create(
                session,
                "inventory-test",
                CollectorKind.SLACK,
                TriggerMode.EVENT,
                None,
                None,
                SlackSelector(("C1",), False),
                None,
                "member:test",
            )
            assert created.inventory_mode is InventoryMode.SENSITIVE_ONLY
        async with harness.sessions.begin() as session:
            updated: CollectorConfig = await service.update(
                session,
                created.id,
                TriggerMode.EVENT,
                None,
                None,
                SlackSelector(("C1",), False),
                None,
                True,
                "member:test",
                InventoryMode.ALL,
            )
            assert updated.inventory_mode is InventoryMode.ALL
        async with harness.sessions.begin() as session:
            kept: CollectorConfig = await service.update(
                session,
                created.id,
                TriggerMode.EVENT,
                None,
                None,
                SlackSelector(("C1",), False),
                None,
                True,
                "member:test",
            )
            assert kept.inventory_mode is InventoryMode.ALL
        async with harness.sessions.begin() as session:
            stopped: CollectorConfig = await service.update(
                session,
                created.id,
                TriggerMode.EVENT,
                None,
                None,
                SlackSelector(("C1",), False),
                None,
                True,
                "member:test",
                InventoryMode.OFF,
            )
            assert stopped.inventory_mode is InventoryMode.OFF
        async with harness.sessions.begin() as session:
            await service.update(
                session,
                created.id,
                TriggerMode.EVENT,
                None,
                None,
                SlackSelector(("C1",), False),
                None,
                True,
                "member:test",
                InventoryMode.OFF,
            )
        assert reconciler.stopped == [(created.id, "member:test")]
        assert all('"inventory_mode"' in dump_json(entry.details) for entry in ports.audits)
    finally:
        await harness.close()
