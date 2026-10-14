"""Audit wire contracts are immutable, versioned and independent of SQLAlchemy."""

import subprocess
import sys
from dataclasses import replace

import pytest

from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonKind, JsonValue, json_array, json_int, json_object, json_str
from telescreen.core.vocabulary import ActorKind, ErrorCode
from telescreen.ports.audit_commands import AuditCommand, decode_audit_command, encode_audit_command
from telescreen.ports.audit_types import AuditEntry
from tests.inventory.handoff.conftest import NOW


def test_round_trip_and_repr_preserve_safe_audit_contract() -> None:
    """Nested immutable details survive encoding while envelope repr omits payloads."""
    command: AuditCommand = AuditCommand(
        "event",
        NOW,
        AuditEntry(
            ActorKind.MEMBER,
            "member:alice",
            "data_asset.updated",
            "data_asset",
            "asset",
            "Private metadata",
            json_object((("details", json_array((json_int(1), json_str("v")))),)),
        ),
    )
    assert decode_audit_command(encode_audit_command(command)) == command
    assert "Private metadata" not in repr(command)
    with pytest.raises(TelescreenError) as failure:
        encode_audit_command(replace(command, event_id=""))
    assert failure.value.code is ErrorCode.VALIDATION_ERROR


@pytest.mark.parametrize("payload", ('{"schema_version":2}', '{"schema_version":true}', "{"))
def test_invalid_wire_data_does_not_echo_input(payload: str) -> None:
    """Reject unsupported and malformed data with a static public error."""
    with pytest.raises(TelescreenError) as failure:
        decode_audit_command(payload)
    assert str(failure.value) == "validation_error: invalid audit command"


def test_audit_contract_imports_without_database_modules() -> None:
    """A future audit transport can load the contract without SQLAlchemy installed."""
    subprocess.run(
        (
            sys.executable,
            "-c",
            "import sys; import telescreen.ports.audit_commands; "
            "assert not any(name.startswith('sqlalchemy') for name in sys.modules)",
        ),
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize("version", ("true", "1.0", '"1"', "2"))
def test_audit_wire_version_requires_supported_strict_integer(version: str) -> None:
    """Valid envelopes cannot smuggle boolean or float schema versions."""
    command: AuditCommand = AuditCommand(
        "event",
        NOW,
        AuditEntry(ActorKind.SYSTEM, "system:test", "test", "test", "id", "Test", json_str("v")),
    )
    payload: str = encode_audit_command(command).replace(
        '"schema_version":1', '"schema_version":' + version
    )
    with pytest.raises(TelescreenError, match="invalid audit command"):
        decode_audit_command(payload)


def test_wire_details_do_not_expose_inactive_internal_json_slots() -> None:
    """The transport carries the public JSON value rather than its storage implementation."""
    command: AuditCommand = AuditCommand(
        "event",
        NOW,
        AuditEntry(
            ActorKind.SYSTEM,
            "system:test",
            "test",
            "test",
            "id",
            "Test",
            JsonValue(JsonKind.NULL, str_value="inactive private text"),
        ),
    )
    payload: str = encode_audit_command(command)
    assert "inactive private text" not in payload
    assert decode_audit_command(payload).entry.details == JsonValue(JsonKind.NULL)
