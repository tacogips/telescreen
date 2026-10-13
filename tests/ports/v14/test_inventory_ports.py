"""Inventory port DTO validation and compatibility tests."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.errors import TelescreenError
from telescreen.core.json import json_null
from telescreen.core.vocabulary import (
    CollectorKind,
    DataAssetKind,
    DetectorKind,
    InventoryMode,
    TriggerMode,
)
from telescreen.ports.collectors import (
    CollectorConfig,
    CustomSelector,
    ItemReport,
    ResourceContent,
    ResourceRef,
    Segment,
)
from telescreen.ports.findings import ExternalScan, ResourceScan
from telescreen.ports.inventory import (
    InventoryObserver,
    InventoryReconciler,
    ItemObservation,
    NoopInventoryObserver,
    NoopInventoryReconciler,
    ResourceObservation,
)
from telescreen.ports.notifications import NotificationContext


def test_item_observation_requires_exactly_one_representation() -> None:
    """Reject absent and conflicting item inventory representations."""
    with pytest.raises(TelescreenError):
        ItemObservation(None, None)
    with pytest.raises(TelescreenError):
        ItemObservation(("a",), 3)
    assert ItemObservation(("a",), None).fingerprints == ("a",)
    assert ItemObservation(None, 3).count == 3


def test_raw_item_keys_are_hidden_from_representation() -> None:
    """Keep provider item keys out of diagnostic representations."""
    assert "secret-key" not in repr(ItemReport(keys=("secret-key",)))


@pytest.mark.asyncio
async def test_noop_observer_and_reconciler_return_none_and_zero() -> None:
    """No-op inventory ports satisfy their protocols and return neutral results."""
    observer: InventoryObserver = NoopInventoryObserver()
    reconciler: InventoryReconciler = NoopInventoryReconciler()
    session: AsyncSession = AsyncSession()
    now: datetime = datetime(2026, 1, 1, tzinfo=UTC)
    observation: ResourceObservation = ResourceObservation(
        "slack",
        "resource-key",
        "masked-locator",
        "Example resource",
        "collector-config",
        "scan-job",
        now,
        True,
        False,
        False,
        (),
        None,
        DataAssetKind.OTHER,
    )
    try:
        observed: None = await observer.observe(session, observation)
        sweep_count: int = await reconciler.reconcile_sweep(session, "cfg", now)
        listing_count: int = await reconciler.reconcile_listing(session, "cfg", frozenset({"k"}))
        stopped_count: int = await reconciler.stop_tracking(session, "cfg", "member:x")
    finally:
        await session.close()
    assert observed is None
    assert sweep_count == 0
    assert listing_count == 0
    assert stopped_count == 0


def test_existing_dto_constructors_keep_working_with_defaults() -> None:
    """Pre-v1.4 positional constructors retain the new fields' defaults."""
    now: datetime = datetime(2026, 1, 1, tzinfo=UTC)
    content: ResourceContent = ResourceContent(
        "slack",
        "config",
        (Segment("line:1", "masked text"),),
        True,
        False,
        "raw-locator",
        "display-title",
        ResourceRef(CollectorKind.SLACK, "{}", "raw-locator"),
    )
    scan: ResourceScan = ResourceScan(
        "slack",
        "resource-key",
        "display-locator",
        "Display title",
        "config",
        "scan",
        "{}",
        (),
        True,
        False,
        now,
    )
    external_scan: ExternalScan = ExternalScan(
        "custom",
        None,
        None,
        DetectorKind.POLICE,
        (),
        (),
        False,
        now,
    )
    config: CollectorConfig = CollectorConfig(
        "id",
        "name",
        CollectorKind.SLACK,
        TriggerMode.BOTH,
        None,
        None,
        4,
        CustomSelector(json_null()),
        "env:SLACK_TOKEN",
        True,
    )
    context: NotificationContext = NotificationContext()

    assert content.items is None and content.asset_kind is None
    assert scan.items is None and scan.asset_kind is None
    assert external_scan.inventories == ()
    assert config.inventory_mode is InventoryMode.SENSITIVE_ONLY
    assert context.data_short_id is None
    assert context.data_name is None
    assert context.data_locator is None
    assert context.data_deletion_due is None
    assert context.data_link is None
