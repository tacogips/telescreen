"""Retention contracts are immutable, revision-specific and independent of persistence."""

import subprocess
import sys
from dataclasses import replace

import pytest

from telescreen.core.errors import TelescreenError
from telescreen.ports.notification_types import NotificationContext
from telescreen.ports.retention import (
    RetentionCommand,
    decode_retention_command,
    encode_retention_command,
    retention_event_id,
)
from tests.inventory.handoff.conftest import NOW


def command() -> RetentionCommand:
    """Create a safe command with deterministic asset/field/revision identity."""
    return RetentionCommand(
        retention_event_id("asset", "field", "revision"),
        NOW,
        "asset",
        "field",
        "revision",
        "manual",
        "r" * 64,
        NotificationContext(
            data_short_id="DATA-1", data_name="Private name", data_deletion_due="2026-10-12"
        ),
    )


def test_retention_round_trip_and_stable_revision_identity() -> None:
    """Retries preserve identity; a genuine value revision creates a new obligation."""
    value: RetentionCommand = command()
    assert decode_retention_command(encode_retention_command(value)) == value
    assert "Private name" not in repr(value)
    assert retention_event_id("asset", "field", "revision") == value.event_id
    assert retention_event_id("asset", "field", "next") != value.event_id
    with pytest.raises(TelescreenError, match="invalid retention command"):
        encode_retention_command(replace(value, due_revision_id="next"))


@pytest.mark.parametrize("version", ("true", "1.0", '"1"', "2"))
def test_retention_requires_a_supported_integer_schema_version(version: str) -> None:
    """Booleans and floats cannot masquerade as the integer schema version."""
    payload: str = encode_retention_command(command()).replace(
        '"schema_version":1', '"schema_version":' + version
    )
    with pytest.raises(TelescreenError) as error:
        decode_retention_command(payload)
    assert str(error.value) == "validation_error: invalid retention command"


def test_notification_contracts_load_without_sqlalchemy() -> None:
    """Future transports can consume the public DTOs without importing an ORM."""
    subprocess.run(
        (
            sys.executable,
            "-c",
            "import sys; import telescreen.ports.retention; "
            "import telescreen.ports.notification_types; "
            "assert not any(name.startswith('sqlalchemy') for name in sys.modules)",
        ),
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
