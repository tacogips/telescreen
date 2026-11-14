"""The assembled ingestion API remains available when inventory delivery fails."""

from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from telescreen.core.vocabulary import ApiScope
from telescreen.db.models import DataAssetRow, IssueRow
from telescreen.directory.tokens import CreatedToken
from telescreen.ports.inventory_commands import InventoryCommand, InventoryReceipt
from tests.e2e.harness import E2EWorld, OutboundRecorder, bearer, make_world
from tests.support.samples import aws_access_key_id


@dataclass(frozen=True)
class UnavailableInventory:
    """Model an unavailable inventory receiver without touching producer persistence."""

    async def receive(self, command: InventoryCommand) -> InventoryReceipt:
        """Every delivery fails until the normal handler is restored."""
        del command
        raise RuntimeError("inventory unavailable")


@pytest.mark.asyncio
async def test_ingestion_commits_while_inventory_is_unavailable_and_recovers(
    tmp_path: Path,
) -> None:
    """Alert/issue writes succeed before inventory projection, which catches up on retry."""
    world: E2EWorld = await make_world(tmp_path, (), OutboundRecorder(), independent_sessions=True)
    try:
        collector: httpx.Response = await world.client.post(
            "/api/collectors",
            headers=bearer(world.admin_token),
            json={
                "name": "handoff",
                "kind": "custom",
                "trigger_mode": "event",
                "cron": None,
                "sweep_cron": None,
                "credential_ref": None,
                "selector_json": "{}",
                "inventory_mode": "all",
                "enabled": True,
            },
        )
        assert collector.status_code == 200, collector.text
        async with world.database.sessions.begin() as session:
            token: CreatedToken = await world.container.token_service.create(
                session, "handoff-ingest", frozenset((ApiScope.INGEST,)), None
            )
        world.container.worker.registry.register(
            replace(world.container.v14.delivery_handler, receiver=UnavailableInventory())
        )
        response: httpx.Response = await world.client.post(
            "/api/ingest/v1/scan",
            headers=bearer(token.token),
            json={
                "source": "custom:handoff",
                "resources": [
                    {
                        "locator": "safe.csv",
                        "complete": True,
                        "removed": False,
                        "segments": [{"locator": "line:1", "text": aws_access_key_id().value}],
                    }
                ],
            },
        )
        assert response.status_code == 200, response.text
        async with world.database.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(IssueRow)) == 1
            assert await session.scalar(select(func.count()).select_from(DataAssetRow)) == 0
        # Exercise the actual queue and its retry path while delivery is unavailable.
        assert await world.container.worker.run_until_idle() >= 1
        async with world.database.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(IssueRow)) == 1
            assert await session.scalar(select(func.count()).select_from(DataAssetRow)) == 0
        world.container.worker.registry.register(world.container.v14.delivery_handler)
        world.clock.advance(timedelta(seconds=11))
        await world.drain()
        async with world.database.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(IssueRow)) == 1
            assert await session.scalar(select(func.count()).select_from(DataAssetRow)) == 1
            assert await world.container.v14.publisher.outbox.pending(session, 100) == ()
    finally:
        await world.close()
