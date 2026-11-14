"""Inventory audit publication survives failures across physically disjoint stores."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from telescreen.audit.receiver import AuditCommandReceiver
from telescreen.audit.service import AuditLogService
from telescreen.audit.verify import verify_chain
from telescreen.core.clock import FakeClock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import json_null, json_object, json_str
from telescreen.core.vocabulary import ActorKind, DataFieldType, EngineTaskKind, EngineTaskStatus
from telescreen.db.base import Base
from telescreen.db.models import (
    AuditChainHeadRow,
    AuditReceiptRow,
    AuditRecordRow,
    DataFieldRow,
    EngineTaskRow,
    InventoryAuditOutboxRow,
)
from telescreen.db.session import create_sessionmaker
from telescreen.integrations.inventory_audit_delivery import (
    InventoryAuditDeliveryHandler,
    InventoryAuditRecoveryProvider,
)
from telescreen.inventory.audit import InventoryAuditOutbox, InventoryAuditPublisher
from telescreen.inventory.fields import FieldService
from telescreen.masking.redact import Redactor
from telescreen.ports.audit_commands import AuditCommand, AuditReceipt, decode_audit_command
from telescreen.ports.audit_types import AuditEntry
from telescreen.ports.workflow import ClaimedTask, TaskResultKind
from telescreen.workflow.queue.sql_queue import SqlTaskQueue
from tests.inventory.handoff.conftest import NOW
from tests.support.samples import Sample, github_token


@dataclass(frozen=True)
class AuditWorld:
    """Inventory contains no audit chain, workflow, directory or issue tables."""

    inventory: async_sessionmaker[AsyncSession]
    audit: async_sessionmaker[AsyncSession]
    clock: FakeClock
    publisher: InventoryAuditPublisher
    outbox: InventoryAuditOutbox
    receiver: AuditCommandReceiver

    async def command(self) -> AuditCommand:
        """Load the only pending command for these focused failure tests."""
        async with self.inventory() as session:
            identifiers: tuple[str, ...] = await self.outbox.pending_ids(session, 10)
            assert len(identifiers) == 1
            command: AuditCommand | None = await self.outbox.get_pending(session, identifiers[0])
        assert command is not None
        return command


@pytest_asyncio.fixture
async def audit_world(tmp_path: Path) -> AsyncIterator[AuditWorld]:
    """Install only each owner's tables in independent SQLite database files."""
    inventory: AsyncEngine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'inventory.db'}")
    audit: AsyncEngine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'audit.db'}")
    try:
        async with inventory.begin() as connection:
            for name in ("data_fields", "inventory_audit_outbox"):
                await connection.run_sync(Base.metadata.tables[name].create)
        async with audit.begin() as connection:
            for name in ("audit_chain_head", "audit_records", "audit_receipts", "engine_tasks"):
                await connection.run_sync(Base.metadata.tables[name].create)
        clock: FakeClock = FakeClock(NOW)
        redactor: Redactor = Redactor.default()
        yield AuditWorld(
            create_sessionmaker(inventory),
            create_sessionmaker(audit),
            clock,
            InventoryAuditPublisher(clock, redactor),
            InventoryAuditOutbox(),
            AuditCommandReceiver(create_sessionmaker(audit), AuditLogService(clock, redactor)),
        )
    finally:
        await inventory.dispose()
        await audit.dispose()


def task(event_id: str) -> ClaimedTask:
    """Use the same immutable event identity for every delivery attempt."""
    return ClaimedTask(
        "task", EngineTaskKind.INVENTORY_AUDIT_DELIVERY, event_id, json_null(), 1, 5, "audit-task"
    )


async def create_field(world: AuditWorld, key: str = "purpose") -> None:
    """Use real inventory business logic rather than appending synthetic outbox rows."""
    fields: FieldService = FieldService(world.publisher, Redactor.default(), world.clock)
    async with world.inventory.begin() as session:
        await fields.create(
            session, key, "Purpose", None, DataFieldType.TEXT, (), False, None, "member:admin"
        )


@pytest.mark.asyncio
async def test_business_change_and_audit_intent_rollback_together(audit_world: AuditWorld) -> None:
    """A failed inventory commit cannot leave an audit intent or chain entry."""
    fields: FieldService = FieldService(
        audit_world.publisher, Redactor.default(), audit_world.clock
    )
    with pytest.raises(RuntimeError, match="rollback"):
        async with audit_world.inventory.begin() as session:
            await fields.create(
                session, "purpose", "Purpose", None, DataFieldType.TEXT, (), False, None, "member:a"
            )
            raise RuntimeError("rollback")
    async with audit_world.inventory() as session:
        assert await session.scalar(select(func.count()).select_from(DataFieldRow)) == 0
        assert await session.scalar(select(func.count()).select_from(InventoryAuditOutboxRow)) == 0
    async with audit_world.audit() as session:
        assert await session.scalar(select(func.count()).select_from(AuditRecordRow)) == 0


@dataclass(frozen=True)
class LoseAuditReply:
    """Simulate a committed audit append whose reply never reaches the worker."""

    receiver: AuditCommandReceiver

    async def receive(self, command: AuditCommand) -> AuditReceipt:
        """Commit first, then fail before inventory acknowledgment."""
        await self.receiver.receive(command)
        raise RuntimeError("lost receipt")


@pytest.mark.asyncio
async def test_lost_reply_retry_is_exactly_once_and_preserves_chain(
    audit_world: AuditWorld,
) -> None:
    """Inventory commits without audit tables; retries never append a second record."""
    await create_field(audit_world)
    command: AuditCommand = await audit_world.command()
    handler: InventoryAuditDeliveryHandler = InventoryAuditDeliveryHandler(
        audit_world.inventory,
        audit_world.outbox,
        LoseAuditReply(audit_world.receiver),
        audit_world.clock,
    )
    with pytest.raises(RuntimeError, match="lost receipt"):
        await handler.handle(task(command.event_id))
    assert await audit_world.command() == command
    audit_world.clock.advance(timedelta(days=1))
    handler = replace(handler, receiver=audit_world.receiver)
    assert (await handler.handle(task(command.event_id))).kind is TaskResultKind.DONE
    assert (await handler.handle(task(command.event_id))).kind is TaskResultKind.DONE
    async with audit_world.inventory() as session:
        assert await audit_world.outbox.pending_ids(session, 10) == ()
        assert await session.scalar(select(func.count()).select_from(DataFieldRow)) == 1
    async with audit_world.audit.begin() as session:
        rows: tuple[AuditRecordRow, ...] = tuple(
            (await session.scalars(select(AuditRecordRow))).all()
        )
        assert len(rows) == 1
        assert rows[0].action == "data_field.created"
        assert ensure_utc(rows[0].at) == command.occurred_at
        assert await session.scalar(select(func.count()).select_from(AuditReceiptRow)) == 1
        await audit_world.receiver.audit.append(
            session,
            AuditEntry(ActorKind.SYSTEM, "system:test", "test", "test", "id", "Later", json_null()),
        )
        assert (await verify_chain(session)).ok


@pytest.mark.asyncio
async def test_receipt_and_chain_rollback_together(audit_world: AuditWorld) -> None:
    """A failed consumer transaction leaves publication pending and retryable."""
    await create_field(audit_world)
    command: AuditCommand = await audit_world.command()
    with pytest.raises(RuntimeError, match="rollback"):
        async with audit_world.audit.begin() as session:
            await audit_world.receiver.audit.append_command(session, command)
            raise RuntimeError("rollback")
    async with audit_world.audit() as session:
        assert await session.scalar(select(func.count()).select_from(AuditRecordRow)) == 0
        assert await session.scalar(select(func.count()).select_from(AuditReceiptRow)) == 0
        head: AuditChainHeadRow | None = await session.get(AuditChainHeadRow, 1)
        assert head is None or head.seq == 0
    assert await audit_world.receiver.receive(command) is AuditReceipt.APPLIED
    assert await audit_world.receiver.receive(command) is AuditReceipt.DUPLICATE
    altered: AuditCommand = replace(command, entry=replace(command.entry, summary="Altered"))
    with pytest.raises(TelescreenError, match="audit event identity conflict"):
        await audit_world.receiver.receive(altered)
    async with audit_world.audit() as session:
        assert (await verify_chain(session)).ok


@pytest.mark.asyncio
async def test_audit_payload_is_masked_before_inventory_storage(audit_world: AuditWorld) -> None:
    """Outbox and receiving chain never persist raw sensitive audit details."""
    sample: Sample = github_token()
    async with audit_world.inventory.begin() as session:
        await audit_world.publisher.append(
            session,
            AuditEntry(
                ActorKind.SYSTEM,
                "system:test",
                "test",
                "test",
                "id",
                sample.value,
                json_object((("value", json_str(sample.value)),)),
            ),
        )
    command: AuditCommand = await audit_world.command()
    async with audit_world.inventory() as session:
        payload: str | None = await session.scalar(select(InventoryAuditOutboxRow.payload_json))
        assert payload is not None and sample.value not in payload
        assert decode_audit_command(payload) == command
    await audit_world.receiver.receive(command)
    async with audit_world.audit() as session:
        record: AuditRecordRow | None = await session.scalar(select(AuditRecordRow))
        assert record is not None
        assert sample.value not in record.summary + record.details_json
        assert (await verify_chain(session)).ok


@pytest.mark.asyncio
async def test_recovery_uses_owned_stores_and_preserves_active_backoff(
    audit_world: AuditWorld,
) -> None:
    """Polling uses inventory reads and main queue writes; exhausted tasks recover."""
    await create_field(audit_world)
    command: AuditCommand = await audit_world.command()
    provider: InventoryAuditRecoveryProvider = InventoryAuditRecoveryProvider(
        audit_world.outbox, SqlTaskQueue(audit_world.clock), audit_world.inventory, batch_size=1
    )
    async with audit_world.audit.begin() as session:
        assert await provider.tick(session, audit_world.clock.now()) == 1
        queued: EngineTaskRow | None = await session.scalar(select(EngineTaskRow))
        assert queued is not None and queued.ref_id == command.event_id
        queued.available_at = audit_world.clock.now() + timedelta(days=1)
    async with audit_world.audit.begin() as session:
        assert await provider.tick(session, audit_world.clock.now()) == 0
        queued = await session.scalar(select(EngineTaskRow))
        assert queued is not None
        queued.status = EngineTaskStatus.DEAD.value
    audit_world.clock.advance(timedelta(minutes=1))
    async with audit_world.audit.begin() as session:
        assert await provider.tick(session, audit_world.clock.now()) == 1
        assert await session.scalar(select(func.count()).select_from(EngineTaskRow)) == 2
