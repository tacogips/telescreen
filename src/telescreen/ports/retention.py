"""Versioned retention delivery and session-free inventory eligibility contracts."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Protocol
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field, TypeAdapter, ValidationError

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode
from telescreen.ports.handoff_limits import require_payload_size
from telescreen.ports.notification_types import NotificationContext


@dataclass(frozen=True)
class RetentionCommand:
    """An overdue obligation identified by asset, field and immutable value revision."""

    event_id: Annotated[str, Field(min_length=1, max_length=36)]
    occurred_at: datetime
    asset_id: Annotated[str, Field(min_length=1, max_length=36)]
    due_field_id: Annotated[str, Field(min_length=1, max_length=36)]
    due_revision_id: Annotated[str, Field(min_length=1, max_length=36)]
    source: str
    resource_key: str
    context: NotificationContext = field(repr=False)
    schema_version: Annotated[int, Field(ge=1, le=1)] = 1


def retention_event_id(asset_id: str, field_id: str, revision_id: str) -> str:
    """Use the same durable identity for every publication of one due-date revision."""
    return str(uuid5(NAMESPACE_URL, f"telescreen:retention:{asset_id}:{field_id}:{revision_id}"))


_COMMAND: TypeAdapter[RetentionCommand] = TypeAdapter(RetentionCommand)


def encode_retention_command(command: RetentionCommand) -> str:
    """Validate the same wire contract on publication and consumption."""
    payload: str = _COMMAND.dump_json(command).decode("utf-8")
    decode_retention_command(payload)
    return payload


def decode_retention_command(payload: str) -> RetentionCommand:
    """Reject unsupported or malformed data without echoing content."""
    require_payload_size(payload)
    try:
        command: RetentionCommand = _COMMAND.validate_json(payload, strict=True)
        if command.event_id != retention_event_id(
            command.asset_id, command.due_field_id, command.due_revision_id
        ):
            raise ValueError("invalid retention identity")
        return command
    except (ValidationError, ValueError):
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid retention command") from None


@dataclass(frozen=True)
class RetentionStatus:
    """Eligibility and the inventory master's current registered owner reference."""

    current: bool
    owner_ref: str | None = None


class RetentionLookup(Protocol):
    """Check inventory state without receiving a notification transaction."""

    async def status(self, command: RetentionCommand) -> RetentionStatus: ...


class RetentionReceipt(StrEnum):
    """A committed notification intent, duplicate, or superseded due-date obligation."""

    APPLIED = "applied"
    DUPLICATE = "duplicate"
    SUPERSEDED = "superseded"


class RetentionReceiver(Protocol):
    """Own notification policy, deduplication and delivery queue transactions."""

    async def receive(self, command: RetentionCommand) -> RetentionReceipt: ...
