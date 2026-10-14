"""Inventory handoff wire contract and invalid-envelope behavior."""

import subprocess
import sys
from datetime import UTC, datetime

import pytest

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import DataAssetKind, ErrorCode, InventoryMode
from telescreen.ports.inventory import ItemObservation, ResourceObservation, ValueFingerprint
from telescreen.ports.inventory_commands import (
    CompleteListing,
    CompleteSweep,
    InventoryCommand,
    InventoryOperation,
    ObserveResource,
    StopTracking,
    decode_inventory_command,
    encode_inventory_command,
)

_NOW: datetime = datetime(2026, 10, 14, tzinfo=UTC)
_OBSERVATION: ResourceObservation = ResourceObservation(
    "custom:records",
    "r" * 64,
    "safe.csv",
    "Records",
    "config",
    "scan",
    _NOW,
    True,
    False,
    True,
    (ValueFingerprint("f" * 64, 1),),
    ItemObservation(("i" * 64,), None),
    DataAssetKind.TABLE,
)


@pytest.mark.parametrize(
    "operation",
    (
        ObserveResource(_OBSERVATION, InventoryMode.ALL),
        CompleteSweep("config", _NOW, InventoryMode.SENSITIVE_ONLY),
        CompleteListing("config", ("a" * 64, "b" * 64), InventoryMode.ALL),
        StopTracking("config", "member:admin"),
    ),
)
def test_inventory_command_wire_round_trip(operation: InventoryOperation) -> None:
    """All handoff operations survive a JSON transport with their typed payload."""
    command: InventoryCommand = InventoryCommand("event", "collector:config", 7, _NOW, operation)
    decoded: InventoryCommand = decode_inventory_command(encode_inventory_command(command))
    assert decoded == command
    assert isinstance(decoded.occurred_at, datetime)


@pytest.mark.parametrize(
    "payload",
    (
        "not JSON",
        "{}",
        '{"event_id":"event","stream_id":"collector:config","sequence":0,'
        '"occurred_at":"2026-10-14T00:00:00Z",'
        '"operation":{"kind":"stop_tracking","collector_config_id":"c","actor_ref":"a"}}',
        '{"event_id":"event","stream_id":"collector:config","sequence":1,'
        '"occurred_at":"2026-10-14T00:00:00Z","schema_version":2,'
        '"operation":{"kind":"stop_tracking","collector_config_id":"c","actor_ref":"a"}}',
        '{"event_id":"event","stream_id":"collector:config","sequence":1,'
        '"occurred_at":"2026-10-14T00:00:00Z","operation":{"kind":"unknown"}}',
        '{"event_id":"event","stream_id":"collector:config","sequence":1,'
        '"occurred_at":"2026-10-14T00:00:00Z",'
        '"operation":{"kind":"listing","collector_config_id":"c","listed_keys":[]}}',
    ),
)
def test_invalid_wire_command_fails_without_echoing_payload(payload: str) -> None:
    """Unknown versions, operations and invalid ordering cannot enter the consumer."""
    with pytest.raises(TelescreenError) as failure:
        decode_inventory_command(payload)
    assert failure.value.code is ErrorCode.VALIDATION_ERROR
    assert str(failure.value) == "validation_error: invalid inventory command"


def test_command_repr_does_not_include_resource_metadata() -> None:
    """Operational envelope logging must not render nested observation data."""
    command: InventoryCommand = InventoryCommand(
        "event", "collector:config", 1, _NOW, ObserveResource(_OBSERVATION, InventoryMode.ALL)
    )
    assert "safe.csv" not in repr(command)
    assert "Records" not in repr(command)


def test_public_inventory_contracts_do_not_import_sqlalchemy() -> None:
    """A future transport process can load public contracts without ORM modules."""
    subprocess.run(
        (
            sys.executable,
            "-c",
            "import sys; "
            "import telescreen.ports.asset_context; "
            "import telescreen.ports.inventory_commands; "
            "import telescreen.ports.inventory_types; "
            "import telescreen.ports.identity_context; "
            "assert not any(name.startswith('sqlalchemy') for name in sys.modules)",
        ),
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize("version", ("true", "1.0", '"1"'))
def test_version_requires_an_integer_on_the_wire(version: str) -> None:
    """JSON booleans, floats and strings must not masquerade as schema version 1."""
    payload: str = (
        '{"event_id":"event","stream_id":"collector:config","sequence":1,'
        '"occurred_at":"2026-10-14T00:00:00Z","schema_version":' + version + ","
        '"operation":{"kind":"stop_tracking","collector_config_id":"c","actor_ref":"a"}}'
    )
    with pytest.raises(TelescreenError) as failure:
        decode_inventory_command(payload)
    assert failure.value.code is ErrorCode.VALIDATION_ERROR
