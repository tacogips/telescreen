"""All durable wire codecs bound UTF-8 payloads and authoritative inventory collections."""

from collections.abc import Callable
from dataclasses import replace

import pytest
from pydantic import TypeAdapter

from telescreen.core.errors import TelescreenError
from telescreen.core.json import json_null
from telescreen.core.vocabulary import ActorKind, InventoryMode
from telescreen.ports import handoff_limits
from telescreen.ports.audit_commands import AuditCommand, decode_audit_command, encode_audit_command
from telescreen.ports.audit_types import AuditEntry
from telescreen.ports.handoff_limits import MAX_INVENTORY_ENTRIES, require_payload_size
from telescreen.ports.inventory_commands import (
    CompleteListing,
    InventoryCommand,
    InventoryOperation,
    ObserveResource,
    decode_inventory_command,
    encode_inventory_command,
)
from telescreen.ports.inventory_types import ItemObservation, ValueFingerprint
from telescreen.ports.retention import (
    RetentionCommand,
    decode_retention_command,
    encode_retention_command,
)
from tests.inventory.handoff.conftest import NOW
from tests.inventory.handoff.test_delivery import observed
from tests.ports.v14.test_retention_commands import command as retention_command


def test_utf8_size_limit_accepts_the_boundary_and_rejects_multibyte_overflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Size is measured in transport bytes, not Python characters."""
    monkeypatch.setattr(handoff_limits, "MAX_HANDOFF_BYTES", 4)
    require_payload_size("éé")
    with pytest.raises(TelescreenError, match="payload exceeds supported limit"):
        require_payload_size("ééa")


@pytest.mark.parametrize(
    "decode", (decode_inventory_command, decode_audit_command, decode_retention_command)
)
def test_every_decoder_rejects_oversize_before_parsing(
    monkeypatch: pytest.MonkeyPatch,
    decode: Callable[[str], InventoryCommand | AuditCommand | RetentionCommand],
) -> None:
    """Malformed oversized data is rejected at the framing boundary without echoing it."""
    monkeypatch.setattr(handoff_limits, "MAX_HANDOFF_BYTES", 4)
    with pytest.raises(TelescreenError, match="payload exceeds supported limit") as failure:
        decode("ééa")
    assert "ééa" not in str(failure.value)


def test_every_encoder_uses_the_same_payload_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """An excessive local publication cannot become durable wire data."""
    inventory: InventoryCommand = InventoryCommand("event", "source:custom", 1, NOW, observed())
    audit: AuditCommand = AuditCommand(
        "event",
        NOW,
        AuditEntry(ActorKind.SYSTEM, "system:test", "test", "asset", "asset", "Safe", json_null()),
    )
    monkeypatch.setattr(handoff_limits, "MAX_HANDOFF_BYTES", 4)
    for encode in (
        lambda: encode_inventory_command(inventory),
        lambda: encode_audit_command(audit),
        lambda: encode_retention_command(retention_command()),
    ):
        with pytest.raises(TelescreenError, match="payload exceeds supported limit"):
            encode()


@pytest.mark.parametrize("kind", ("listing", "items", "values"))
def test_inventory_collection_limits_apply_to_publication_and_consumption(kind: str) -> None:
    """Reject a whole oversized snapshot rather than truncating authoritative members."""
    entries: tuple[str, ...] = ("f" * 64,) * (MAX_INVENTORY_ENTRIES + 1)
    operation: InventoryOperation
    if kind == "listing":
        operation = CompleteListing("config", entries, InventoryMode.ALL)
    elif kind == "items":
        operation = ObserveResource(
            replace(observed().observation, items=ItemObservation(entries, None)), InventoryMode.ALL
        )
    else:
        operation = ObserveResource(
            replace(observed().observation, values=(ValueFingerprint("f" * 64, 1),) * len(entries)),
            InventoryMode.ALL,
        )
    value: InventoryCommand = InventoryCommand("event", "collector:config", 1, NOW, operation)
    with pytest.raises(TelescreenError, match="snapshot exceeds supported limit"):
        encode_inventory_command(value)
    payload: str = TypeAdapter(InventoryCommand).dump_json(value).decode()
    with pytest.raises(TelescreenError, match="snapshot exceeds supported limit"):
        decode_inventory_command(payload)


def test_full_listing_at_the_entry_limit_round_trips_without_truncation() -> None:
    """The stated supported maximum remains usable as one complete authoritative listing."""
    value: InventoryCommand = InventoryCommand(
        "event",
        "collector:config",
        1,
        NOW,
        CompleteListing("config", ("f" * 64,) * MAX_INVENTORY_ENTRIES, InventoryMode.ALL),
    )
    assert decode_inventory_command(encode_inventory_command(value)) == value
