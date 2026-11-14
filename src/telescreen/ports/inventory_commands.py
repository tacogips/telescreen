"""Versioned inventory write commands, independent of persistence and transport."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import Field, TypeAdapter, ValidationError

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode, InventoryMode
from telescreen.ports.handoff_limits import MAX_INVENTORY_ENTRIES, require_payload_size
from telescreen.ports.inventory_types import ResourceObservation


class InventoryCommandKind(StrEnum):
    """Inventory operations requiring ordered, durable delivery."""

    OBSERVE = "observe"
    SWEEP = "sweep"
    LISTING = "listing"
    STOP_TRACKING = "stop_tracking"


@dataclass(frozen=True)
class ObserveResource:
    """Capture safe observation data and the producer's inventory mode."""

    observation: ResourceObservation
    inventory_mode: InventoryMode
    kind: Literal[InventoryCommandKind.OBSERVE] = InventoryCommandKind.OBSERVE


@dataclass(frozen=True)
class CompleteSweep:
    """Reconcile a completed sweep after all preceding stream commands."""

    collector_config_id: str
    seen_before: datetime
    inventory_mode: InventoryMode
    kind: Literal[InventoryCommandKind.SWEEP] = InventoryCommandKind.SWEEP


@dataclass(frozen=True)
class CompleteListing:
    """Carry a complete listing, never a partial or skipped listing."""

    collector_config_id: str
    listed_keys: tuple[str, ...]
    inventory_mode: InventoryMode
    kind: Literal[InventoryCommandKind.LISTING] = InventoryCommandKind.LISTING


@dataclass(frozen=True)
class StopTracking:
    """Apply the producer's tracking change at its ordered stream position."""

    collector_config_id: str
    actor_ref: str
    kind: Literal[InventoryCommandKind.STOP_TRACKING] = InventoryCommandKind.STOP_TRACKING


type InventoryOperation = Annotated[
    ObserveResource | CompleteSweep | CompleteListing | StopTracking,
    Field(discriminator="kind"),
]


@dataclass(frozen=True)
class InventoryCommand:
    """Transport envelope; retries must preserve the ID and stream sequence."""

    event_id: Annotated[str, Field(min_length=1, max_length=36)]
    stream_id: Annotated[str, Field(min_length=1, max_length=256)]
    sequence: Annotated[int, Field(ge=1)]
    occurred_at: datetime
    operation: InventoryOperation = field(repr=False)
    schema_version: Annotated[int, Field(ge=1, le=1)] = 1


_COMMAND: TypeAdapter[InventoryCommand] = TypeAdapter(InventoryCommand)


def encode_inventory_command(command: InventoryCommand) -> str:
    """Encode a typed command using the same validation rules as its consumer."""
    _require_collection_sizes(command)
    encoded: str = _COMMAND.dump_json(command).decode("utf-8")
    decode_inventory_command(encoded)
    return encoded


def decode_inventory_command(payload: str) -> InventoryCommand:
    """Reject malformed or unsupported wire data without echoing its content."""
    require_payload_size(payload)
    try:
        command: InventoryCommand = _COMMAND.validate_json(payload, strict=True)
        _require_collection_sizes(command)
        return command
    except ValidationError:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid inventory command") from None


def _require_collection_sizes(command: InventoryCommand) -> None:
    """A full snapshot either fits as a whole or fails; never truncate authoritative data."""
    operation: InventoryOperation = command.operation
    sizes: tuple[int, ...] = ()
    if isinstance(operation, CompleteListing):
        sizes = (len(operation.listed_keys),)
    elif isinstance(operation, ObserveResource):
        sizes = (len(operation.observation.values),)
        if operation.observation.items is not None:
            sizes += (len(operation.observation.items.fingerprints or ()),)
    if any(size > MAX_INVENTORY_ENTRIES for size in sizes):
        raise TelescreenError(
            ErrorCode.VALIDATION_ERROR, "inventory snapshot exceeds supported limit"
        )


class InventoryReceipt(StrEnum):
    """A consumer receipt is returned only after its inventory transaction commits."""

    APPLIED = "applied"
    DUPLICATE = "duplicate"
    GAP = "gap"


class InventoryCommandReceiver(Protocol):
    """Receive a value-only command using the inventory module's own transaction."""

    async def receive(self, command: InventoryCommand) -> InventoryReceipt: ...
