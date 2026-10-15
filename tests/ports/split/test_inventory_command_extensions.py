"""Tests for additive Inventory command kinds in schema version one."""

from datetime import UTC, datetime

from telescreen.ports.directory_projection import DirectoryEntity, DirectoryEntityKind
from telescreen.ports.inventory_commands import (
    DirectoryUpdate,
    InventoryCommand,
    IssueLinkState,
    decode_inventory_command,
    encode_inventory_command,
)

NOW: datetime = datetime(2026, 10, 15, tzinfo=UTC)


def test_issue_link_and_directory_commands_round_trip() -> None:
    """Both new operation kinds remain compatible with the existing envelope version."""
    issue: InventoryCommand = InventoryCommand(
        "event-1",
        "issue-link:issue-1",
        1,
        NOW,
        IssueLinkState("issue-1", "resource-1", "ISSUE-1", "open", None, True),
    )
    assert decode_inventory_command(encode_inventory_command(issue)) == issue
    entity: DirectoryEntity = DirectoryEntity(
        DirectoryEntityKind.MEMBER, "member-1", 1, False, None
    )
    directory: InventoryCommand = InventoryCommand(
        "event-2", "directory", 1, NOW, DirectoryUpdate(entity)
    )
    assert decode_inventory_command(encode_inventory_command(directory)) == directory
