"""Real audit receiver concurrency and database ownership checks."""

import asyncio

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import ProgrammingError

from telescreen.audit.chain import GENESIS_HASH
from telescreen.audit.receiver import AuditCommandReceiver
from telescreen.audit.service import AuditLogService
from telescreen.audit.verify import verify_chain
from telescreen.core.json import json_null
from telescreen.core.vocabulary import ActorKind
from telescreen.db.models import AuditChainHeadRow, AuditReceiptRow, AuditRecordRow
from telescreen.inventory.audit import InventoryAuditOutbox, InventoryAuditPublisher
from telescreen.masking.redact import Redactor
from telescreen.ports.audit_commands import AuditCommand, AuditReceipt
from telescreen.ports.audit_types import AuditEntry
from tests.inventory.handoff.conftest import NOW, HandoffWorld
from tests.inventory.handoff.pg_fixture import PgHandoff, pg_handoff

__all__: list[str] = ["pg_handoff"]
pytestmark: list[pytest.MarkDecorator] = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.mark.parametrize("seeded_head", (False, True))
async def test_duplicate_delivery_and_local_appends_share_one_chain(
    pg_handoff: PgHandoff, seeded_head: bool
) -> None:
    """Even simultaneous first writers serialize one receipt with all local appends."""
    world: HandoffWorld = pg_handoff.world
    service: AuditLogService = AuditLogService(world.clock, Redactor.default())
    receiver: AuditCommandReceiver = AuditCommandReceiver(world.producer, service)
    entry: AuditEntry = AuditEntry(
        ActorKind.SYSTEM,
        "system:test",
        "data_field.created",
        "data_field",
        "id",
        "Created",
        json_null(),
    )
    if seeded_head:
        async with world.producer.begin() as session:
            session.add(AuditChainHeadRow(id=1, seq=0, hash=GENESIS_HASH))
    publisher: InventoryAuditPublisher = InventoryAuditPublisher(world.clock, Redactor.default())
    outbox: InventoryAuditOutbox = InventoryAuditOutbox()
    async with world.inventory.begin() as session:
        await publisher.append(session, entry)
    async with world.inventory() as session:
        identifiers: tuple[str, ...] = await outbox.pending_ids(session, 10)
        command: AuditCommand | None = await outbox.get_pending(session, identifiers[0])
    assert command is not None
    delivered: AuditCommand = command

    async def local_append() -> None:
        async with world.producer.begin() as session:
            await service.append(session, entry)

    async def append_locals() -> None:
        await asyncio.gather(*(local_append() for _ in range(12)))

    async def duplicates() -> tuple[AuditReceipt, ...]:
        return tuple(await asyncio.gather(*(receiver.receive(delivered) for _ in range(24))))

    results: tuple[AuditReceipt, ...]
    _, results = await asyncio.gather(append_locals(), duplicates())
    assert results.count(AuditReceipt.APPLIED) == 1
    assert results.count(AuditReceipt.DUPLICATE) == 23
    async with world.producer() as session:
        assert await session.scalar(select(func.count()).select_from(AuditRecordRow)) == 13
        assert await session.scalar(select(func.count()).select_from(AuditReceiptRow)) == 1
        assert (await verify_chain(session)).ok
    async with world.inventory() as session:
        assert await outbox.get_pending(session, command.event_id) is not None
    async with world.inventory.begin() as session:
        await outbox.acknowledge(session, command.event_id, NOW)
    async with world.inventory() as session:
        assert await outbox.pending_ids(session, 10) == ()
    async with world.inventory() as session:
        with pytest.raises(ProgrammingError):
            await session.execute(
                text(f'SELECT * FROM "{pg_handoff.producer_schema}".audit_records')
            )
    async with world.producer() as session:
        with pytest.raises(ProgrammingError):
            await session.execute(
                text(f'SELECT * FROM "{pg_handoff.inventory_schema}".inventory_audit_outbox')
            )
