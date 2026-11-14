"""Persistence adapter for a versioned directory replica."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.engine import ScalarResult
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.db.models.projections import (
    DirectoryEntryModel,
    InventoryDirectoryEntryRow,
    NotificationDirectoryEntryRow,
)
from telescreen.ports.directory_projection import (
    ApiTokenEntity,
    CollectorEntity,
    DirectoryEntity,
    DirectoryEntityKind,
    DirectoryPayload,
    IdentityEntity,
    MemberEntity,
    OwnershipRulesEntity,
    TeamEntity,
    decode_directory_entity,
    encode_directory_entity,
)

type DirectoryEntryRow = InventoryDirectoryEntryRow | NotificationDirectoryEntryRow


def _lookup_key(entity: DirectoryEntity) -> str | None:
    """Return the kind-specific indexed lookup value for a live entity."""
    payload: DirectoryPayload | None = entity.payload
    if isinstance(payload, MemberEntity):
        return payload.handle
    if isinstance(payload, TeamEntity):
        return payload.slug
    if isinstance(payload, IdentityEntity):
        return f"{payload.identity_kind}:{payload.identity_key}"
    if isinstance(payload, ApiTokenEntity):
        return payload.token_hash
    if isinstance(payload, (CollectorEntity, OwnershipRulesEntity)):
        return None
    return None


@dataclass(frozen=True)
class DirectoryReplica:
    """Read and apply revisioned entities in Inventory or Notification storage."""

    model: DirectoryEntryModel

    async def apply(self, session: AsyncSession, entity: DirectoryEntity) -> bool:
        """Apply only strictly newer versions, retaining tombstones as barriers."""
        row: DirectoryEntryRow | None = await session.get(
            self.model, (entity.kind.value, entity.id)
        )
        if row is not None and entity.revision <= row.revision:
            return False
        if row is None:
            row = self.model(
                kind=entity.kind.value,
                id=entity.id,
                revision=entity.revision,
                present=entity.present,
                lookup_key=_lookup_key(entity) if entity.present else None,
                payload_json=encode_directory_entity(entity),
                applied_at=datetime.now(UTC),
            )
            session.add(row)
        else:
            row.revision = entity.revision
            row.present = entity.present
            row.lookup_key = _lookup_key(entity) if entity.present else None
            row.payload_json = encode_directory_entity(entity)
            row.applied_at = datetime.now(UTC)
        await session.flush()
        return True

    async def get(
        self, session: AsyncSession, kind: DirectoryEntityKind, entity_id: str
    ) -> DirectoryEntity | None:
        """Return one live entity by its stable identifier."""
        row: DirectoryEntryRow | None = await session.get(self.model, (kind.value, entity_id))
        if row is None or not row.present:
            return None
        return decode_directory_entity(row.payload_json)

    async def by_lookup(
        self, session: AsyncSession, kind: DirectoryEntityKind, key: str
    ) -> DirectoryEntity | None:
        """Return one live entity by its kind-specific indexed key."""
        row: DirectoryEntryRow | None = await session.scalar(
            select(self.model)
            .where(
                self.model.kind == kind.value,
                self.model.lookup_key == key,
                self.model.present.is_(True),
            )
            .order_by(self.model.id)
            .limit(1)
        )
        return decode_directory_entity(row.payload_json) if row is not None else None

    async def members_of_team(
        self, session: AsyncSession, team_id: str
    ) -> tuple[MemberEntity, ...]:
        """Return live team members in stable handle order."""
        team_entity: DirectoryEntity | None = await self.get(
            session, DirectoryEntityKind.TEAM, team_id
        )
        if team_entity is None or not isinstance(team_entity.payload, TeamEntity):
            return ()
        members: list[MemberEntity] = []
        for member_id in team_entity.payload.member_ids:
            entity: DirectoryEntity | None = await self.get(
                session, DirectoryEntityKind.MEMBER, member_id
            )
            if entity is not None and isinstance(entity.payload, MemberEntity):
                members.append(entity.payload)
        return tuple(sorted(members, key=lambda member: member.handle))

    async def identities_of_member(
        self, session: AsyncSession, member_id: str
    ) -> tuple[IdentityEntity, ...]:
        """Return a member's live identities in stable identity order."""
        rows: ScalarResult[DirectoryEntryRow] = await session.scalars(
            select(self.model)
            .where(
                self.model.kind == DirectoryEntityKind.IDENTITY.value, self.model.present.is_(True)
            )
            .order_by(self.model.id)
        )
        identities: list[IdentityEntity] = []
        for row in rows:
            entity: DirectoryEntity = decode_directory_entity(row.payload_json)
            if isinstance(entity.payload, IdentityEntity) and entity.payload.member_id == member_id:
                identities.append(entity.payload)
        return tuple(identities)

    async def watermark(self, session: AsyncSession) -> int:
        """Return the greatest applied revision, or zero for an empty replica."""
        value: int | None = await session.scalar(select(func.max(self.model.revision)))
        return value or 0
