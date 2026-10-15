"""Versioned notification commands shared by independent service processes."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import (
    ErrorCode,
    NotificationCategory,
    NotificationChannel,
    NotificationEvent,
)
from telescreen.ports.directory_projection import DirectoryEntity, encode_directory_entity
from telescreen.ports.handoff_limits import require_payload_size
from telescreen.ports.notification_types import (
    STRING_CONTEXT_FIELDS,
    ContextFieldHits,
    NotificationContext,
    ProducerRedactionHits,
)

MAX_NOTIFICATION_OPERATIONS: int = 8
MAX_NOTIFICATION_RECIPIENTS: int = 64


class _StrictWireModel:
    """Forbid unrecognized fields on notification command dataclasses."""

    __pydantic_config__ = ConfigDict(extra="forbid")


class NotificationCommandKind(StrEnum):
    """Notification operations accepted by the consumer."""

    SEND = "send"
    MEMBER_SEND = "member_send"
    DIRECTORY = "directory"


@dataclass(frozen=True)
class SendOperation(_StrictWireModel):
    """One masked direct notification request."""

    channel: NotificationChannel
    recipients: Annotated[
        tuple[str, ...], Field(min_length=1, max_length=MAX_NOTIFICATION_RECIPIENTS)
    ]
    subject: str
    body: str
    actor_ref: str
    run_id: str | None
    issue_id: str | None
    event: NotificationEvent | None
    template_name: str | None
    context: NotificationContext | None
    inline: bool
    set_name: str | None
    target_index: int | None
    record_unreachable: bool
    category: NotificationCategory | None
    skip_unknown: bool
    redaction: ProducerRedactionHits
    kind: Literal[NotificationCommandKind.SEND] = NotificationCommandKind.SEND


@dataclass(frozen=True)
class MemberSendOperation(_StrictWireModel):
    """One member-directed category notification."""

    member_id: str
    category: NotificationCategory
    event: NotificationEvent
    context: NotificationContext
    issue_id: str | None
    redaction: ProducerRedactionHits
    kind: Literal[NotificationCommandKind.MEMBER_SEND] = NotificationCommandKind.MEMBER_SEND


@dataclass(frozen=True)
class DirectoryOperation(_StrictWireModel):
    """Apply a revisioned directory entity at the notification consumer."""

    entity: DirectoryEntity
    kind: Literal[NotificationCommandKind.DIRECTORY] = NotificationCommandKind.DIRECTORY


type NotificationOperation = Annotated[
    SendOperation | MemberSendOperation | DirectoryOperation,
    Field(discriminator="kind"),
]


@dataclass(frozen=True)
class NotificationCommand(_StrictWireModel):
    """Atomic batch of notification operations with an idempotent event identity."""

    event_id: Annotated[str, Field(min_length=1, max_length=36)]
    occurred_at: datetime
    operations: Annotated[
        tuple[NotificationOperation, ...],
        Field(min_length=1, max_length=MAX_NOTIFICATION_OPERATIONS),
    ]
    schema_version: Annotated[int, Field(ge=1, le=1)] = 1


class NotificationCommandReceipt(StrEnum):
    """Durable notification command outcomes understood by outbox senders."""

    APPLIED = "applied"
    DUPLICATE = "duplicate"
    DEFERRED = "deferred"


@dataclass(frozen=True)
class ChannelStatus(_StrictWireModel):
    """Configured delivery channel flags returned by the notification service."""

    email: bool
    slack: bool


@dataclass(frozen=True)
class TestRequest(_StrictWireModel):
    """Request a channel test for a directory member."""

    channel: NotificationChannel
    member_id: str


_COMMAND: TypeAdapter[NotificationCommand] = TypeAdapter(NotificationCommand)


def encode_notification_command(command: NotificationCommand) -> str:
    """Encode a typed notification command using consumer-side checks."""
    _validate_command(command)
    encoded: str = _COMMAND.dump_json(command).decode("utf-8")
    decode_notification_command(encoded)
    return encoded


def decode_notification_command(payload: str) -> NotificationCommand:
    """Decode bounded wire data without reflecting its contents in errors."""
    require_payload_size(payload)
    try:
        command: NotificationCommand = _COMMAND.validate_json(payload, strict=True)
        _validate_command(command)
        return command
    except (ValidationError, ValueError):
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid notification command") from None


def _validate_command(command: NotificationCommand) -> None:
    """Enforce count-only producer redaction metadata constraints."""
    for operation in command.operations:
        if isinstance(operation, DirectoryOperation):
            encode_directory_entity(operation.entity)
            continue
        redaction: ProducerRedactionHits | None = None
        if isinstance(operation, (SendOperation, MemberSendOperation)):
            redaction = operation.redaction
        if redaction is None:
            continue
        fields: tuple[str, ...] = tuple(hit.field for hit in redaction.context)
        if (
            redaction.subject < 0
            or redaction.body < 0
            or any(hit.count < 1 for hit in redaction.context)
            or len(fields) > len(STRING_CONTEXT_FIELDS)
            or len(set(fields)) != len(fields)
            or any(name not in STRING_CONTEXT_FIELDS for name in fields)
        ):
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid notification command")
        if isinstance(operation, MemberSendOperation) and (redaction.subject or redaction.body):
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid notification command")


def operation_seed(event_id: str, op_index: int) -> str:
    """Return the deterministic seed for one operation in an event."""
    return f"{event_id}:{op_index}"


def notification_row_id(id_seed: str, recipient_index: int) -> str:
    """Derive a stable notification row id for one recipient position."""
    return str(uuid5(NAMESPACE_URL, f"telescreen:notification:{id_seed}:{recipient_index}"))


__all__: list[str] = [
    "ChannelStatus",
    "ContextFieldHits",
    "DirectoryOperation",
    "MemberSendOperation",
    "NotificationCommand",
    "NotificationCommandKind",
    "NotificationCommandReceipt",
    "NotificationContext",
    "NotificationOperation",
    "ProducerRedactionHits",
    "SendOperation",
    "TestRequest",
    "decode_notification_command",
    "encode_notification_command",
    "notification_row_id",
    "operation_seed",
]
