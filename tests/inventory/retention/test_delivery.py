"""Real notification policy and failure handling across disjoint inventory/main stores."""

from dataclasses import dataclass, replace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.audit.verify import verify_chain
from telescreen.core.errors import TelescreenError
from telescreen.core.json import json_null
from telescreen.core.vocabulary import EngineTaskKind, NotificationCategory
from telescreen.db.models import (
    AuditRecordRow,
    DataAssetRow,
    DataFieldRow,
    DataFieldValueRow,
    EngineTaskRow,
    InventoryRetentionOutboxRow,
    NotificationRow,
    NotificationSettingRow,
    RetentionNotificationReceiptRow,
)
from telescreen.integrations.retention_delivery import RetentionDeliveryHandler
from telescreen.inventory.retention import _DueAsset
from telescreen.notifications.retention import RetentionNotificationReceiver
from telescreen.ports.notifications import MemberNotification, NotificationReceipt
from telescreen.ports.retention import RetentionCommand, RetentionReceipt, RetentionStatus
from telescreen.ports.workflow import ClaimedTask
from tests.inventory.retention.conftest import RetentionWorld
from tests.support.samples import github_token


def task(command: RetentionCommand) -> ClaimedTask:
    """Describe the durable command's delivery attempt."""
    return ClaimedTask(
        "task", EngineTaskKind.RETENTION_DELIVERY, command.event_id, json_null(), 1, 5, "delivery"
    )


@dataclass(frozen=True)
class LoseReply:
    """Lose a receipt only after the real receiver committed its notification intent."""

    world: RetentionWorld

    async def receive(self, command: RetentionCommand) -> RetentionReceipt:
        """Crash before inventory acknowledgment."""
        await self.world.receiver.receive(command)
        raise RuntimeError("lost reply")


@pytest.mark.asyncio
async def test_publication_and_lost_receipt_retry_use_only_owned_tables(
    retention_world: RetentionWorld,
) -> None:
    """Inventory commits without notification access and retry queues exactly one notice."""
    world: RetentionWorld = retention_world
    await world.seed(owner="member:alice")
    commands: tuple[RetentionCommand, ...] = await world.publish()
    assert len(commands) == 1
    async with world.main.begin() as session:
        assert await session.scalar(select(func.count()).select_from(NotificationRow)) == 0
        session.add(
            NotificationSettingRow(
                member_id="member-id",
                category=NotificationCategory.ASSIGNED.value,
                channel="slack",
                updated_at=world.clock.now(),
            )
        )
    delivery: RetentionDeliveryHandler = RetentionDeliveryHandler(
        world.inventory, world.outbox, LoseReply(world), world.clock
    )
    with pytest.raises(RuntimeError, match="lost reply"):
        await delivery.handle(task(commands[0]))
    async with world.inventory() as session:
        assert await world.outbox.get_pending(session, commands[0].event_id) == commands[0]
        asset: DataAssetRow | None = await session.get(DataAssetRow, "asset")
        assert asset is not None and asset.overdue_notified_for == "2026-10-12"
    delivery = replace(delivery, receiver=world.receiver)
    await delivery.handle(task(commands[0]))
    await delivery.handle(task(commands[0]))
    async with world.main() as session:
        notices: tuple[NotificationRow, ...] = tuple(
            (await session.scalars(select(NotificationRow))).all()
        )
        assert len(notices) == 1 and notices[0].channel == "slack"
        assert notices[0].category == "assigned" and notices[0].event_kind == "data_overdue"
        assert (
            await session.scalar(select(func.count()).select_from(RetentionNotificationReceiptRow))
            == 1
        )
        assert await session.scalar(select(func.count()).select_from(EngineTaskRow)) == 1
        assert (await verify_chain(session)).ok
    async with world.inventory() as session:
        assert await world.outbox.pending_ids(session, 10) == ()


@dataclass(frozen=True)
class FailAfterNotify:
    """Roll back after the real sender has staged rows, audit and delivery tasks."""

    world: RetentionWorld

    async def send_to_member(
        self, session: AsyncSession, notification: MemberNotification
    ) -> NotificationReceipt:
        """A receipt must never survive a failed notification transaction."""
        await self.world.receiver.members.send_to_member(session, notification)
        raise RuntimeError("consumer rollback")


@pytest.mark.asyncio
async def test_consumer_failure_rolls_back_receipt_notification_queue_and_audit(
    retention_world: RetentionWorld,
) -> None:
    """Retryable notification failure leaves published inventory state intact."""
    world: RetentionWorld = retention_world
    await world.seed()
    command: RetentionCommand = (await world.publish())[0]
    failed: RetentionNotificationReceiver = replace(world.receiver, members=FailAfterNotify(world))
    with pytest.raises(RuntimeError, match="consumer rollback"):
        await failed.receive(command)
    async with world.main() as session:
        assert await session.scalar(select(func.count()).select_from(NotificationRow)) == 0
        assert (
            await session.scalar(select(func.count()).select_from(RetentionNotificationReceiptRow))
            == 0
        )
        assert await session.scalar(select(func.count()).select_from(EngineTaskRow)) == 0
        assert await session.scalar(select(func.count()).select_from(AuditRecordRow)) == 0
    async with world.inventory() as session:
        assert await world.outbox.get_pending(session, command.event_id) is not None
    assert await world.receiver.receive(command) is RetentionReceipt.APPLIED
    assert await world.receiver.receive(command) is RetentionReceipt.DUPLICATE
    altered: RetentionCommand = replace(
        command, context=replace(command.context, data_name="Changed")
    )
    with pytest.raises(TelescreenError, match="retention event identity conflict"):
        await world.receiver.receive(altered)


@pytest.mark.asyncio
async def test_same_clock_date_edits_create_distinct_obligations_and_skip_stale_events(
    retention_world: RetentionWorld,
) -> None:
    """A -> B -> A rotates identity even at the same timestamp; only the current revision sends."""
    world: RetentionWorld = retention_world
    await world.seed()
    first: RetentionCommand = (await world.publish())[0]
    await world.change_due("2026-10-11")
    second: RetentionCommand = next(
        command for command in await world.publish() if command.event_id != first.event_id
    )
    await world.change_due("2026-10-12")
    third: RetentionCommand = next(
        command
        for command in await world.publish()
        if command.event_id not in (first.event_id, second.event_id)
    )
    assert len({first.due_revision_id, second.due_revision_id, third.due_revision_id}) == 3
    assert first.occurred_at == second.occurred_at == third.occurred_at
    assert await world.receiver.receive(first) is RetentionReceipt.SUPERSEDED
    assert await world.receiver.receive(second) is RetentionReceipt.SUPERSEDED
    assert await world.receiver.receive(third) is RetentionReceipt.APPLIED
    await world.change_due("2026-10-12")
    assert len(await world.publish()) == 3
    async with world.inventory() as session:
        value: DataFieldValueRow | None = await session.get(
            DataFieldValueRow, ("asset", "due-field")
        )
        assert value is not None and value.revision_id == third.due_revision_id
    async with world.main() as session:
        assert await session.scalar(select(func.count()).select_from(NotificationRow)) == 1
        assert (await verify_chain(session)).ok


@pytest.mark.parametrize("change", ("clear", "future", "archive", "delete"))
@pytest.mark.asyncio
async def test_invalidated_obligations_are_receipted_without_notices(
    retention_world: RetentionWorld, change: str
) -> None:
    """Clear, defer, archive and delete invalidate an already queued overdue obligation."""
    world: RetentionWorld = retention_world
    await world.seed()
    command: RetentionCommand = (await world.publish())[0]
    if change in ("clear", "future"):
        await world.change_due(None if change == "clear" else "2027-01-01")
    else:
        async with world.inventory.begin() as session:
            if change == "archive":
                field: DataFieldRow | None = await session.get(DataFieldRow, "due-field")
                assert field is not None
                field.archived_at = world.clock.now()
            else:
                asset: DataAssetRow | None = await session.get(DataAssetRow, "asset")
                assert asset is not None
                asset.state = "deleted"
    assert await world.receiver.receive(command) is RetentionReceipt.SUPERSEDED
    assert await world.receiver.receive(command) is RetentionReceipt.DUPLICATE
    async with world.main() as session:
        assert await session.scalar(select(func.count()).select_from(NotificationRow)) == 0
        assert (
            await session.scalar(select(func.count()).select_from(RetentionNotificationReceiptRow))
            == 1
        )


@pytest.mark.parametrize("fallback,expected", (("member:alice", 1), ("team:ops", 2), (None, 0)))
@pytest.mark.asyncio
async def test_stale_registered_owner_uses_current_fallback_policy(
    retention_world: RetentionWorld, fallback: str | None, expected: int
) -> None:
    """Recipient resolution and team fanout happen exclusively in the main database."""
    world: RetentionWorld = retention_world
    world.owners.fallback = fallback
    await world.seed(owner="member:retired")
    command: RetentionCommand = (await world.publish())[0]
    assert await world.receiver.receive(command) is RetentionReceipt.APPLIED
    async with world.main() as session:
        assert await session.scalar(select(func.count()).select_from(NotificationRow)) == expected
    assert world.owners.calls == 1


@pytest.mark.asyncio
async def test_current_master_owner_and_off_preference_are_honored(
    retention_world: RetentionWorld,
) -> None:
    """The master owner's latest reference and current preference win over queued context."""
    world: RetentionWorld = retention_world
    await world.seed(owner="member:retired")
    command: RetentionCommand = (await world.publish())[0]
    async with world.inventory.begin() as session:
        owner: DataFieldValueRow | None = await session.get(
            DataFieldValueRow, ("asset", "owner-field")
        )
        assert owner is not None
        owner.value_json = '"member:alice"'
    async with world.main.begin() as session:
        session.add(
            NotificationSettingRow(
                member_id="member-id",
                category="assigned",
                channel="off",
                updated_at=world.clock.now(),
            )
        )
    assert await world.receiver.receive(command) is RetentionReceipt.APPLIED
    assert world.owners.calls == 0
    async with world.main() as session:
        notice: NotificationRow | None = await session.scalar(select(NotificationRow))
        assert notice is not None and notice.status == "skipped"
        assert await session.scalar(select(func.count()).select_from(EngineTaskRow)) == 0


@pytest.mark.asyncio
async def test_masking_before_publication_and_dlp_before_notification_persistence(
    retention_world: RetentionWorld,
) -> None:
    """The outbox masks metadata, and the receiver retains the normal outbound DLP gate."""
    world: RetentionWorld = retention_world
    await world.seed(owner="member:alice")
    secret: str = github_token().value
    async with world.inventory.begin() as session:
        asset: DataAssetRow | None = await session.get(DataAssetRow, "asset")
        assert asset is not None
        asset.name = secret
    command: RetentionCommand = (await world.publish())[0]
    async with world.inventory() as session:
        payload: str | None = await session.scalar(select(InventoryRetentionOutboxRow.payload_json))
        assert payload is not None and secret not in payload
    malicious: RetentionCommand = replace(
        command, context=replace(command.context, data_name=secret)
    )
    assert await world.receiver.receive(malicious) is RetentionReceipt.APPLIED
    async with world.main() as session:
        notice: NotificationRow | None = await session.scalar(select(NotificationRow))
        assert notice is not None and notice.status == "blocked"
        assert secret not in notice.subject + notice.body
        assert await session.scalar(select(func.count()).select_from(EngineTaskRow)) == 0


@pytest.mark.asyncio
async def test_inventory_marker_and_publication_rollback_together(
    retention_world: RetentionWorld,
) -> None:
    """A failed inventory transaction leaves the due value eligible for the next scan."""
    world: RetentionWorld = retention_world
    await world.seed()
    with pytest.raises(RuntimeError, match="publisher rollback"):
        async with world.inventory.begin() as session:
            await world.publisher._publish(session, _DueAsset("asset", "2026-10-12"))
            raise RuntimeError("publisher rollback")
    async with world.inventory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(InventoryRetentionOutboxRow)) == 0
        )
        asset: DataAssetRow | None = await session.get(DataAssetRow, "asset")
        assert asset is not None and asset.overdue_notified_for is None
    command: RetentionCommand = (await world.publish())[0]
    assert await world.receiver.receive(command) is RetentionReceipt.APPLIED


@pytest.mark.asyncio
async def test_changing_due_field_role_publishes_new_obligation_for_same_date(
    retention_world: RetentionWorld,
) -> None:
    """A new due-field identity cannot be hidden by the old field's date marker."""
    from telescreen.core.vocabulary import DataFieldRole, DataFieldType
    from telescreen.inventory.fields import FieldService
    from telescreen.masking.redact import Redactor

    world: RetentionWorld = retention_world
    await world.seed()
    original: RetentionCommand = (await world.publish())[0]
    assert await world.receiver.receive(original) is RetentionReceipt.APPLIED
    fields: FieldService = FieldService(world.values.audit, Redactor.default(), world.clock)
    async with world.inventory.begin() as session:
        alternate: DataFieldRow = await fields.create(
            session,
            "alternate",
            "Alternate date",
            None,
            DataFieldType.DATE,
            (),
            False,
            None,
            "member:admin",
        )
        alternate_id: str = alternate.id
        session.add(
            DataFieldValueRow(
                asset_id="asset",
                field_id=alternate_id,
                value_json='"2026-10-12"',
                value_date="2026-10-12",
                updated_by="member:admin",
                updated_at=world.clock.now(),
            )
        )
        await fields.update(
            session,
            "due-field",
            "Deletion due",
            None,
            DataFieldType.DATE,
            (),
            False,
            None,
            "member:admin",
        )
        await fields.update(
            session,
            alternate_id,
            "Alternate date",
            None,
            DataFieldType.DATE,
            (),
            False,
            DataFieldRole.DELETION_DUE,
            "member:admin",
        )
    replacement: RetentionCommand = next(
        command for command in await world.publish() if command.due_field_id == alternate_id
    )
    assert replacement.event_id != original.event_id
    assert await world.receiver.receive(replacement) is RetentionReceipt.APPLIED
    async with world.main() as session:
        assert await session.scalar(select(func.count()).select_from(NotificationRow)) == 2


@dataclass(frozen=True)
class UnavailableInventoryLookup:
    """Fail new eligibility reads while allowing already committed receipts to be reused."""

    async def status(self, command: RetentionCommand) -> RetentionStatus:
        """An inventory outage must not change committed notification receipt semantics."""
        del command
        raise RuntimeError("inventory unavailable")


@pytest.mark.asyncio
async def test_committed_receipt_can_be_acknowledged_while_inventory_is_down(
    retention_world: RetentionWorld,
) -> None:
    """Only unreceived obligations need inventory eligibility reads during retry."""
    world: RetentionWorld = retention_world
    await world.seed()
    command: RetentionCommand = (await world.publish())[0]
    unavailable: RetentionNotificationReceiver = replace(
        world.receiver, inventory=UnavailableInventoryLookup()
    )
    with pytest.raises(RuntimeError, match="inventory unavailable"):
        await unavailable.receive(command)
    async with world.main() as session:
        assert (
            await session.scalar(select(func.count()).select_from(RetentionNotificationReceiptRow))
            == 0
        )
    assert await world.receiver.receive(command) is RetentionReceipt.APPLIED
    assert await unavailable.receive(command) is RetentionReceipt.DUPLICATE
