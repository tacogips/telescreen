"""Notification-only directory adapters and durable delivery task queue."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import EngineTaskKind, ErrorCode
from telescreen.db.models import NotificationDeliveryStateRow
from telescreen.notifications.member_send import MemberReferences
from telescreen.notifications.preferences import MemberContact, PreferenceRepository
from telescreen.ports.directory_projection import DirectoryEntity, DirectoryEntityKind, MemberEntity
from telescreen.ports.workflow import TaskQueue, TaskSpec
from telescreen.replica.store import DirectoryReplica


@dataclass(frozen=True)
class ReplicaMemberReferences(MemberReferences):
    """Resolve active member identifiers from Notification's directory replica."""

    replica: DirectoryReplica

    async def member_reference(self, session: AsyncSession, member_id: str) -> str:
        """Return the replicated member handle or recipient_unknown."""
        entity: DirectoryEntity | None = await self.replica.get(
            session, DirectoryEntityKind.MEMBER, member_id
        )
        if (
            entity is None
            or not isinstance(entity.payload, MemberEntity)
            or not entity.payload.active
        ):
            raise TelescreenError(ErrorCode.RECIPIENT_UNKNOWN, "member recipient is unavailable")
        return f"member:{entity.payload.handle}"


@dataclass(frozen=True)
class ReplicaPreferenceMembers:
    """Read only contact availability from Notification's pushed directory."""

    replica: DirectoryReplica

    async def member(self, session: AsyncSession, member_id: str) -> MemberContact | None:
        """Return the minimal contact DTO for an active replicated member."""
        entity: DirectoryEntity | None = await self.replica.get(
            session, DirectoryEntityKind.MEMBER, member_id
        )
        if (
            entity is None
            or not isinstance(entity.payload, MemberEntity)
            or not entity.payload.active
        ):
            return None
        return MemberContact(
            entity.payload.id,
            entity.payload.email is not None,
            entity.payload.slack_user_id is not None,
        )


@dataclass(frozen=True)
class ReplicaPreferenceRepository(PreferenceRepository):
    """Combine Notification-owned settings with replica-backed member contacts."""

    replica: DirectoryReplica

    async def member(self, session: AsyncSession, member_id: str) -> MemberContact | None:
        """Return member contact availability without querying the main-store table."""
        return await ReplicaPreferenceMembers(self.replica).member(session, member_id)


@dataclass(frozen=True)
class NotificationTaskQueue(TaskQueue):
    """Persist notification delivery schedules in the Notification-owned store."""

    async def enqueue(self, session: AsyncSession, spec: TaskSpec) -> bool:
        """Insert one delivery schedule, preserving an existing idempotency key."""
        if spec.kind is not EngineTaskKind.NOTIFICATION_DELIVERY:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "unsupported notification task")
        row: NotificationDeliveryStateRow | None = await session.get(
            NotificationDeliveryStateRow, spec.ref_id
        )
        if row is not None:
            return False
        session.add(
            NotificationDeliveryStateRow(
                notification_id=spec.ref_id,
                attempt=0,
                max_attempts=spec.max_attempts,
                available_at=spec.available_at,
                leased_until=None,
            )
        )
        await session.flush()
        return True

    async def cancel_by_ref(self, session: AsyncSession, ref_id: str) -> int:
        """Remove a pending notification schedule."""
        row: NotificationDeliveryStateRow | None = await session.get(
            NotificationDeliveryStateRow, ref_id
        )
        if row is None:
            return 0
        await session.delete(row)
        return 1
