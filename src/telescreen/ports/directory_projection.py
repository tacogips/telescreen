"""Versioned, persistence-independent directory projection values."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode
from telescreen.ports.handoff_limits import require_payload_size


class _StrictWireModel:
    """Forbid unrecognized fields on every dataclass in the wire contract."""

    __pydantic_config__ = ConfigDict(extra="forbid")


class DirectoryEntityKind(StrEnum):
    """Kinds of entities published to service replicas."""

    MEMBER = "member"
    TEAM = "team"
    IDENTITY = "identity"
    API_TOKEN = "api_token"
    OWNERSHIP_RULES = "ownership_rules"
    COLLECTOR = "collector"


@dataclass(frozen=True)
class MemberEntity(_StrictWireModel):
    """Projected member identity and optional notification addresses."""

    id: str
    handle: str
    display_name: str
    active: bool
    role: str | None
    email: str | None
    slack_user_id: str | None
    kind: Literal[DirectoryEntityKind.MEMBER] = DirectoryEntityKind.MEMBER


@dataclass(frozen=True)
class TeamEntity(_StrictWireModel):
    """Projected team membership and optional notification addresses."""

    id: str
    slug: str
    name: str
    lead_member_id: str | None
    member_ids: tuple[str, ...]
    email: str | None
    slack_channel: str | None
    kind: Literal[DirectoryEntityKind.TEAM] = DirectoryEntityKind.TEAM


@dataclass(frozen=True)
class IdentityEntity(_StrictWireModel):
    """Projected keyed identity fingerprint."""

    id: str
    member_id: str
    identity_kind: str
    identity_key: str
    kind: Literal[DirectoryEntityKind.IDENTITY] = DirectoryEntityKind.IDENTITY


@dataclass(frozen=True)
class ApiTokenEntity(_StrictWireModel):
    """Projected API token verifier and permission metadata."""

    id: str
    label: str
    token_hash: str
    scopes: tuple[str, ...]
    member_id: str | None
    revoked: bool
    kind: Literal[DirectoryEntityKind.API_TOKEN] = DirectoryEntityKind.API_TOKEN


@dataclass(frozen=True)
class OwnershipRuleEntry(_StrictWireModel):
    """One ordered ownership routing rule."""

    id: str
    position: int
    source: str | None
    pattern: str
    team_id: str
    enabled: bool


@dataclass(frozen=True)
class OwnershipRulesEntity(_StrictWireModel):
    """Complete ordered ownership rule set."""

    rules: tuple[OwnershipRuleEntry, ...]
    kind: Literal[DirectoryEntityKind.OWNERSHIP_RULES] = DirectoryEntityKind.OWNERSHIP_RULES


@dataclass(frozen=True)
class CollectorEntity(_StrictWireModel):
    """Projected collector display name."""

    id: str
    name: str
    kind: Literal[DirectoryEntityKind.COLLECTOR] = DirectoryEntityKind.COLLECTOR


type DirectoryPayload = Annotated[
    MemberEntity
    | TeamEntity
    | IdentityEntity
    | ApiTokenEntity
    | OwnershipRulesEntity
    | CollectorEntity,
    Field(discriminator="kind"),
]


@dataclass(frozen=True)
class DirectoryEntity(_StrictWireModel):
    """Revisioned entity state; absent entities are explicit tombstones."""

    kind: DirectoryEntityKind
    id: Annotated[str, Field(min_length=1, max_length=36)]
    revision: Annotated[int, Field(ge=1)]
    present: bool
    payload: DirectoryPayload | None = None


_ENTITY: TypeAdapter[DirectoryEntity] = TypeAdapter(DirectoryEntity)


def encode_directory_entity(entity: DirectoryEntity) -> str:
    """Encode an entity after applying the consumer's strict validation rules."""
    encoded: str = _ENTITY.dump_json(entity).decode("utf-8")
    decode_directory_entity(encoded)
    return encoded


def decode_directory_entity(payload: str) -> DirectoryEntity:
    """Decode a bounded entity without reflecting malformed wire content."""
    require_payload_size(payload)
    try:
        entity: DirectoryEntity = _ENTITY.validate_json(payload, strict=True)
    except ValidationError:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid directory entity") from None
    if entity.present != (entity.payload is not None):
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid directory entity")
    if entity.payload is not None and entity.payload.kind is not entity.kind:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid directory entity")
    if entity.kind is DirectoryEntityKind.OWNERSHIP_RULES and entity.id != "rules":
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid directory entity")
    return entity


def for_inventory(entity: DirectoryEntity) -> DirectoryEntity:
    """Remove delivery addresses from member and team projections."""
    payload: DirectoryPayload | None = entity.payload
    if isinstance(payload, MemberEntity):
        payload = MemberEntity(
            payload.id,
            payload.handle,
            payload.display_name,
            payload.active,
            payload.role,
            None,
            None,
        )
    elif isinstance(payload, TeamEntity):
        payload = TeamEntity(
            payload.id,
            payload.slug,
            payload.name,
            payload.lead_member_id,
            payload.member_ids,
            None,
            None,
        )
    return DirectoryEntity(entity.kind, entity.id, entity.revision, entity.present, payload)
