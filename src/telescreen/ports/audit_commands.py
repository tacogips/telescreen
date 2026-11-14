"""Versioned audit delivery contract without database sessions or ORM imports."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Protocol

from pydantic import Field, TypeAdapter, ValidationError

from telescreen.core.errors import TelescreenError
from telescreen.core.json import dump_json, parse_json
from telescreen.core.vocabulary import ActorKind, ErrorCode
from telescreen.ports.audit_types import AuditEntry
from telescreen.ports.handoff_limits import require_payload_size


@dataclass(frozen=True)
class AuditCommand:
    """An immutable audit identity and occurrence time retained across retries."""

    event_id: str
    occurred_at: datetime
    entry: AuditEntry = field(repr=False)
    schema_version: int = 1


@dataclass(frozen=True)
class _WireAuditCommand:
    """Keep JSON details in their canonical public form rather than internal value slots."""

    event_id: Annotated[str, Field(min_length=1, max_length=36)]
    occurred_at: datetime
    actor_kind: ActorKind
    actor_ref: Annotated[str, Field(max_length=256)]
    action: Annotated[str, Field(max_length=128)]
    target_kind: Annotated[str, Field(max_length=64)]
    target_id: Annotated[str, Field(max_length=36)]
    summary: str
    details_json: str
    schema_version: Annotated[int, Field(ge=1, le=1)] = 1


_COMMAND: TypeAdapter[_WireAuditCommand] = TypeAdapter(_WireAuditCommand)


def encode_audit_command(command: AuditCommand) -> str:
    """Encode and validate the same contract used by the receiver."""
    wire: _WireAuditCommand = _WireAuditCommand(
        command.event_id,
        command.occurred_at,
        command.entry.actor_kind,
        command.entry.actor_ref,
        command.entry.action,
        command.entry.target_kind,
        command.entry.target_id,
        command.entry.summary,
        dump_json(command.entry.details),
        command.schema_version,
    )
    encoded: str = _COMMAND.dump_json(wire).decode("utf-8")
    decode_audit_command(encoded)
    return encoded


def decode_audit_command(payload: str) -> AuditCommand:
    """Reject invalid wire data without exposing its contents."""
    require_payload_size(payload)
    try:
        wire: _WireAuditCommand = _COMMAND.validate_json(payload, strict=True)
        return AuditCommand(
            wire.event_id,
            wire.occurred_at,
            AuditEntry(
                wire.actor_kind,
                wire.actor_ref,
                wire.action,
                wire.target_kind,
                wire.target_id,
                wire.summary,
                parse_json(wire.details_json),
            ),
            wire.schema_version,
        )
    except (ValidationError, TelescreenError):
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid audit command") from None


class AuditReceipt(StrEnum):
    """Receipts describe a committed audit transaction."""

    APPLIED = "applied"
    DUPLICATE = "duplicate"


class AuditReceiver(Protocol):
    """Append using the audit owner's transaction, never the caller's session."""

    async def receive(self, command: AuditCommand) -> AuditReceipt: ...
