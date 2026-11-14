"""Assembled inventory writes remain available during audit delivery outages."""

from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from telescreen.audit.verify import verify_chain
from telescreen.db.models import AuditRecordRow, DataFieldRow, InventoryAuditOutboxRow
from telescreen.ports.audit_commands import AuditCommand, AuditReceipt
from tests.e2e.harness import E2EWorld, OutboundRecorder, bearer, make_world


@dataclass(frozen=True)
class UnavailableAudit:
    """Reject delivery without opening or modifying the inventory database."""

    async def receive(self, command: AuditCommand) -> AuditReceipt:
        """Leave the durable inventory intent available for retry."""
        del command
        raise RuntimeError("audit unavailable")


@pytest.mark.asyncio
async def test_inventory_commits_during_audit_outage_and_chain_catches_up(tmp_path: Path) -> None:
    """An actual worker retries the audit entry without rolling back the field definition."""
    world: E2EWorld = await make_world(tmp_path, (), OutboundRecorder(), independent_sessions=True)
    try:
        world.container.worker.registry.register(
            replace(world.container.v14.audit_delivery_handler, receiver=UnavailableAudit())
        )
        response: httpx.Response = await world.client.post(
            "/api/data/fields",
            headers=bearer(world.admin_token),
            json={"key": "purpose", "label": "Purpose", "field_type": "text"},
        )
        assert response.status_code == 201, response.text
        async with world.database.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(DataFieldRow)) == 1
            assert (
                await session.scalar(select(func.count()).select_from(InventoryAuditOutboxRow)) == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AuditRecordRow)
                    .where(AuditRecordRow.action == "data_field.created")
                )
                == 0
            )
        assert await world.container.worker.run_until_idle() >= 1
        async with world.database.sessions() as session:
            pending: InventoryAuditOutboxRow | None = await session.scalar(
                select(InventoryAuditOutboxRow)
            )
            assert pending is not None and pending.delivered_at is None
            assert await session.scalar(select(func.count()).select_from(DataFieldRow)) == 1
        world.container.worker.registry.register(world.container.v14.audit_delivery_handler)
        world.clock.advance(timedelta(seconds=11))
        await world.drain()
        async with world.database.sessions() as session:
            pending = await session.scalar(select(InventoryAuditOutboxRow))
            assert pending is not None and pending.delivered_at is not None
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AuditRecordRow)
                    .where(AuditRecordRow.action == "data_field.created")
                )
                == 1
            )
            assert (await verify_chain(session)).ok
    finally:
        await world.close()
