"""Crash, retry and sequencing guarantees across disjoint databases."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode, InventoryMode, IssueStatus
from telescreen.db.models import DataAssetEventRow, DataAssetRow, IssueRow
from telescreen.db.models.inventory_handoff import InventoryInboxRow, InventoryOutboxRow
from telescreen.integrations.inventory_dispatch import InventoryDispatcher
from telescreen.inventory.handoff.inbox import InventoryInbox
from telescreen.ports.inventory_commands import (
    CompleteListing,
    CompleteSweep,
    InventoryCommand,
    InventoryReceipt,
    ObserveResource,
    StopTracking,
)
from telescreen.ports.inventory_types import ItemObservation, ResourceObservation
from tests.inventory.handoff.conftest import NOW, HandoffWorld
from tests.issues.v14_asset_owner.test_resolver import _issue


def observed(at: datetime = NOW, key: str = "a" * 64) -> ObserveResource:
    """Build an observation containing only already-safe metadata and fingerprints."""
    return ObserveResource(
        ResourceObservation(
            "custom",
            key,
            "safe.csv",
            "Report",
            "config-id",
            None,
            at,
            True,
            False,
            True,
            (),
            ItemObservation(None, 3),
            None,
        ),
        InventoryMode.ALL,
    )


@dataclass
class CrashAfterCommit:
    """Simulate a response lost after inventory committed its transaction."""

    inbox: InventoryInbox
    crashed: bool = False

    async def receive(self, command: InventoryCommand) -> InventoryReceipt:
        """The first delivery commits and then loses its acknowledgment."""
        receipt: InventoryReceipt = await self.inbox.receive(command)
        if not self.crashed:
            self.crashed = True
            raise RuntimeError("simulated lost receipt")
        return receipt


@dataclass
class FailAfterApply:
    """Simulate a consumer crash after mutations but before the inbox commit."""

    world: HandoffWorld
    fail: bool = True

    async def apply(self, session: AsyncSession, command: InventoryCommand) -> None:
        """Exercise actual inventory writes before aborting the transaction."""
        await self.world.applier.apply(session, command)
        if self.fail:
            raise RuntimeError("simulated consumer crash")


@pytest.mark.asyncio
async def test_producer_rollback_keeps_business_and_handoff_atomic(handoff: HandoffWorld) -> None:
    """An aborted issue write cannot publish inventory or consume its stream position."""
    with pytest.raises(RuntimeError, match="producer crash"):
        async with handoff.producer.begin() as session:
            session.add(_issue(IssueStatus.UNRESOLVED, None, NOW))
            await handoff.outbox.append(session, "collector:config-id", NOW, observed())
            raise RuntimeError("producer crash")
    async with handoff.producer() as session:
        assert await session.get(IssueRow, "issue-1") is None
        assert await handoff.outbox.pending(session, 100) == ()
    command: InventoryCommand = await handoff.append(observed())
    assert command.sequence == 1
    assert await handoff.dispatcher.dispatch() == 1
    async with handoff.inventory() as session:
        asset: DataAssetRow | None = await session.scalar(select(DataAssetRow))
        assert asset is not None
        assert asset.item_count == 3


@pytest.mark.asyncio
async def test_lost_ack_retries_without_duplicate_inventory_history(handoff: HandoffWorld) -> None:
    """Crash between the two commits leaves durable replay work and one inventory effect."""
    command: InventoryCommand = await handoff.append(observed())
    dispatcher: InventoryDispatcher = replace(
        handoff.dispatcher, receiver=CrashAfterCommit(handoff.inbox)
    )
    assert await dispatcher.dispatch() == 0
    async with handoff.producer() as session:
        assert await handoff.outbox.pending(session, 10) == (command,)
    assert await dispatcher.dispatch() == 1
    assert await dispatcher.dispatch() == 0
    async with handoff.inventory() as session:
        assert await session.scalar(select(func.count()).select_from(DataAssetRow)) == 1
        assert await session.scalar(select(func.count()).select_from(DataAssetEventRow)) == 1
        assert await session.scalar(select(func.count()).select_from(InventoryInboxRow)) == 1
    async with handoff.producer() as session:
        outbox: InventoryOutboxRow | None = await session.get(InventoryOutboxRow, command.event_id)
        assert outbox is not None and outbox.delivered_at is not None


@pytest.mark.asyncio
async def test_consumer_failure_rolls_back_asset_history_and_receipt(handoff: HandoffWorld) -> None:
    """A command is replayable when inventory failed before its single local commit."""
    command: InventoryCommand = await handoff.append(observed())
    applier: FailAfterApply = FailAfterApply(handoff)
    inbox: InventoryInbox = InventoryInbox(handoff.inventory, applier, handoff.clock)
    with pytest.raises(RuntimeError, match="consumer crash"):
        await inbox.receive(command)
    async with handoff.inventory() as session:
        assert await session.scalar(select(func.count()).select_from(DataAssetRow)) == 0
        assert await session.scalar(select(func.count()).select_from(DataAssetEventRow)) == 0
        assert await session.scalar(select(func.count()).select_from(InventoryInboxRow)) == 0
    applier.fail = False
    assert await inbox.receive(command) is InventoryReceipt.APPLIED
    assert await inbox.receive(command) is InventoryReceipt.DUPLICATE
    async with handoff.inventory() as session:
        asset: DataAssetRow | None = await session.scalar(select(DataAssetRow))
        assert asset is not None and asset.number == 1


@pytest.mark.asyncio
async def test_sweep_cannot_overtake_a_missing_observation(handoff: HandoffWorld) -> None:
    """The sweep barrier waits for the observation that keeps its resource present."""
    old: InventoryCommand = await handoff.append(observed(NOW - timedelta(days=1)))
    current: InventoryCommand = await handoff.append(observed())
    sweep: InventoryCommand = await handoff.append(
        CompleteSweep("config-id", NOW, InventoryMode.ALL)
    )
    assert await handoff.inbox.receive(old) is InventoryReceipt.APPLIED
    assert await handoff.inbox.receive(sweep) is InventoryReceipt.GAP
    async with handoff.inventory() as session:
        asset: DataAssetRow | None = await session.scalar(select(DataAssetRow))
        assert asset is not None and asset.state == "present"
    assert await handoff.inbox.receive(current) is InventoryReceipt.APPLIED
    assert await handoff.inbox.receive(sweep) is InventoryReceipt.APPLIED
    assert await handoff.inbox.receive(sweep) is InventoryReceipt.DUPLICATE
    async with handoff.inventory() as session:
        asset = await session.scalar(select(DataAssetRow))
        assert asset is not None and asset.state == "present"


@pytest.mark.asyncio
async def test_conflicting_identity_or_stream_position_is_rejected(handoff: HandoffWorld) -> None:
    """An event ID or stream position cannot be reused for a different operation."""
    command: InventoryCommand = await handoff.append(observed())
    assert await handoff.inbox.receive(command) is InventoryReceipt.APPLIED
    for conflicting in (
        replace(command, operation=StopTracking("config-id", "member:admin")),
        replace(command, event_id="different-event"),
        replace(command, stream_id="different-stream"),
    ):
        with pytest.raises(TelescreenError) as failure:
            await handoff.inbox.receive(conflicting)
        assert failure.value.code is ErrorCode.CONFLICT
    async with handoff.inventory() as session:
        assert await session.scalar(select(func.count()).select_from(InventoryInboxRow)) == 1
        asset: DataAssetRow | None = await session.scalar(select(DataAssetRow))
        assert asset is not None and asset.state == "present"


@pytest.mark.asyncio
async def test_listing_stop_and_stale_observations_use_captured_context(
    handoff: HandoffWorld,
) -> None:
    """Replay never reads collector tables or resurrects an asset using an older scan."""
    discovery: InventoryCommand = await handoff.append(observed())
    assert await handoff.inbox.receive(discovery) is InventoryReceipt.APPLIED
    deleted_at: datetime = NOW + timedelta(days=1)
    listing: InventoryCommand = await handoff.append(
        CompleteListing("config-id", (), InventoryMode.ALL), deleted_at
    )
    assert await handoff.inbox.receive(listing) is InventoryReceipt.APPLIED
    stale: InventoryCommand = await handoff.append(observed(), deleted_at)
    assert await handoff.inbox.receive(stale) is InventoryReceipt.APPLIED
    async with handoff.inventory() as session:
        asset: DataAssetRow | None = await session.scalar(select(DataAssetRow))
        assert asset is not None and asset.state == "deleted"
    later: datetime = deleted_at + timedelta(hours=1)
    restore: InventoryCommand = await handoff.append(observed(later), later)
    assert await handoff.inbox.receive(restore) is InventoryReceipt.APPLIED
    stopped: InventoryCommand = await handoff.append(
        StopTracking("config-id", "member:admin"), later
    )
    assert await handoff.inbox.receive(stopped) is InventoryReceipt.APPLIED
    stale = await handoff.append(observed(), later)
    assert await handoff.inbox.receive(stale) is InventoryReceipt.APPLIED
    async with handoff.inventory() as session:
        asset = await session.scalar(select(DataAssetRow))
        assert asset is not None and asset.state == "unknown"


@pytest.mark.asyncio
async def test_captured_off_mode_does_not_discover_an_asset(handoff: HandoffWorld) -> None:
    """An older command retains its policy rather than consulting today's configuration."""
    command: InventoryCommand = await handoff.append(
        replace(observed(), inventory_mode=InventoryMode.OFF)
    )
    assert await handoff.inbox.receive(command) is InventoryReceipt.APPLIED
    async with handoff.inventory() as session:
        assert await session.scalar(select(func.count()).select_from(DataAssetRow)) == 0
        assert await session.scalar(select(func.count()).select_from(InventoryInboxRow)) == 1


@dataclass(frozen=True)
class FailOneStream:
    """Keep one stream unavailable while another remains deliverable."""

    inbox: InventoryInbox

    async def receive(self, command: InventoryCommand) -> InventoryReceipt:
        """An unavailable stream must not occupy an entire delivery batch."""
        if command.stream_id == "collector:broken":
            raise RuntimeError("simulated unavailable stream")
        return await self.inbox.receive(command)


@pytest.mark.asyncio
async def test_failed_stream_backlog_does_not_hide_other_streams(handoff: HandoffWorld) -> None:
    """Only a stream head is selected, so its backlog cannot fill a bounded batch."""
    for _ in range(5):
        await handoff.append(observed(), stream_id="collector:broken")
    await handoff.append(observed(key="b" * 64), stream_id="collector:working")
    dispatcher: InventoryDispatcher = replace(
        handoff.dispatcher, receiver=FailOneStream(handoff.inbox)
    )
    assert await dispatcher.dispatch(limit=2) == 1
    async with handoff.inventory() as session:
        asset: DataAssetRow | None = await session.scalar(select(DataAssetRow))
        assert asset is not None and asset.resource_key == "b" * 64
    async with handoff.producer() as session:
        pending: tuple[InventoryCommand, ...] = await handoff.outbox.pending(session, 2)
        assert len(pending) == 1 and pending[0].stream_id == "collector:broken"
    # Recovery is durable: each new dispatch advances the failed stream by one position.
    for _ in range(5):
        assert await handoff.dispatcher.dispatch(limit=2) == 1
    assert await handoff.dispatcher.dispatch(limit=2) == 0
