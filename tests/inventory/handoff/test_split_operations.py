"""Ordered issue-link and directory operations stay atomic with inbox receipts."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from telescreen.core.errors import TelescreenError
from telescreen.db.models.inventory_handoff import InventoryInboxRow
from telescreen.ports.directory_projection import (
    ApiTokenEntity,
    DirectoryEntity,
    DirectoryEntityKind,
)
from telescreen.ports.inventory_commands import (
    DirectoryUpdate,
    InventoryCommand,
    IssueLinkState,
)
from tests.inventory.handoff.conftest import HandoffWorld


@pytest.mark.parametrize(
    "operation",
    (
        IssueLinkState("issue-id", "resource-key", "ISSUE-1", "unresolved", None, True),
        DirectoryUpdate(
            DirectoryEntity(
                DirectoryEntityKind.API_TOKEN,
                "token-id",
                1,
                True,
                ApiTokenEntity("token-id", "token", "a" * 64, ("read",), None, False),
            )
        ),
    ),
)
async def test_missing_projection_sink_does_not_acknowledge(
    handoff: HandoffWorld, operation: IssueLinkState | DirectoryUpdate
) -> None:
    """Unsupported projection kinds fail inside the transaction and leave no receipt."""
    command: InventoryCommand = InventoryCommand(
        "event-id",
        "projection-stream",
        1,
        datetime(2026, 10, 14, tzinfo=UTC),
        operation,
    )
    with pytest.raises(TelescreenError):
        await handoff.inbox.receive(command)
    async with handoff.inventory() as session:
        receipt: InventoryInboxRow | None = await session.scalar(
            select(InventoryInboxRow).where(InventoryInboxRow.id == command.event_id)
        )
    assert receipt is None
