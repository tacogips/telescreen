"""Directory ports implemented from a service-local directory replica."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.engine import ScalarResult
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ActorDisplayKind, ErrorCode, IdentityKind
from telescreen.ports.directory import (
    SLACK_CHANNEL_ID,
    ActorDirectory,
    AssigneeIds,
    OwnerDirectory,
    RecipientResolver,
    ResolvedRecipients,
)
from telescreen.ports.directory_projection import (
    ApiTokenEntity,
    CollectorEntity,
    DirectoryEntity,
    DirectoryEntityKind,
    IdentityEntity,
    MemberEntity,
    TeamEntity,
    decode_directory_entity,
)
from telescreen.ports.identity_context import ActorDisplay
from telescreen.replica.store import DirectoryEntryRow, DirectoryReplica


@dataclass(frozen=True)
class ReplicaRecipientResolver(RecipientResolver):
    """Resolve recipient and assignment references without a Telescreen database."""

    replica: DirectoryReplica

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients:
        kind: str
        separator: str
        value: str
        kind, separator, value = ref.partition(":")
        if not separator or not value:
            raise _unknown_recipient()
        if kind == "email":
            return ResolvedRecipients((value,), (), (), ())
        if kind == "slack_channel" and SLACK_CHANNEL_ID.fullmatch(value) is None:
            raise _unknown_recipient()
        if kind in ("slack", "slack_channel"):
            return ResolvedRecipients((), (value,), (), ())
        if kind == "member":
            entity: DirectoryEntity | None = await self.replica.by_lookup(
                session, DirectoryEntityKind.MEMBER, value
            )
            if (
                entity is None
                or not isinstance(entity.payload, MemberEntity)
                or not entity.payload.active
            ):
                raise _unknown_recipient()
            member: MemberEntity = entity.payload
            return ResolvedRecipients(
                (member.email,) if member.email is not None else (),
                (),
                (member.slack_user_id,) if member.slack_user_id is not None else (),
                (member.id,),
            )
        if kind == "team":
            entity: DirectoryEntity | None = await self.replica.by_lookup(
                session, DirectoryEntityKind.TEAM, value
            )
            if entity is None or not isinstance(entity.payload, TeamEntity):
                raise _unknown_recipient()
            team: TeamEntity = entity.payload
            members: tuple[MemberEntity, ...] = await self.replica.members_of_team(session, team.id)
            emails: list[str] = [member.email for member in members if member.email is not None]
            if team.email is not None:
                emails.append(team.email)
            channels: tuple[str, ...] = (team.slack_channel,) if team.slack_channel else ()
            slack_ids: tuple[str, ...] = tuple(
                member.slack_user_id for member in members if member.slack_user_id is not None
            )
            return ResolvedRecipients(
                tuple(dict.fromkeys(emails)), channels, slack_ids, tuple(m.id for m in members)
            )
        raise _unknown_recipient()

    async def is_member_of(self, session: AsyncSession, member_id: str, ref: str) -> bool:
        kind: str
        separator: str
        value: str
        kind, separator, value = ref.partition(":")
        if not separator:
            return False
        if kind == "member":
            entity: DirectoryEntity | None = await self.replica.by_lookup(
                session, DirectoryEntityKind.MEMBER, value
            )
            return (
                entity is not None
                and isinstance(entity.payload, MemberEntity)
                and entity.payload.active
                and entity.payload.id == member_id
            )
        if kind == "team":
            entity: DirectoryEntity | None = await self.replica.by_lookup(
                session, DirectoryEntityKind.TEAM, value
            )
            return (
                entity is not None
                and isinstance(entity.payload, TeamEntity)
                and member_id in entity.payload.member_ids
            )
        return False

    async def assignee_of(self, session: AsyncSession, ref: str) -> AssigneeIds:
        kind: str
        separator: str
        value: str
        kind, separator, value = ref.partition(":")
        if separator and kind == "member":
            entity: DirectoryEntity | None = await self.replica.by_lookup(
                session, DirectoryEntityKind.MEMBER, value
            )
            if (
                entity is not None
                and isinstance(entity.payload, MemberEntity)
                and entity.payload.active
            ):
                return AssigneeIds(entity.payload.id, None)
        if separator and kind == "team":
            entity: DirectoryEntity | None = await self.replica.by_lookup(
                session, DirectoryEntityKind.TEAM, value
            )
            if entity is not None and isinstance(entity.payload, TeamEntity):
                return AssigneeIds(None, entity.payload.id)
        raise _unknown_recipient()


@dataclass(frozen=True)
class ReplicaActorDirectory(ActorDirectory):
    """Resolve actor references from replica entities, preserving input order."""

    replica: DirectoryReplica

    async def display(
        self, session: AsyncSession, refs: tuple[str, ...]
    ) -> tuple[ActorDisplay, ...]:
        output: list[ActorDisplay] = []
        for ref in refs:
            kind: str
            separator: str
            key: str
            kind, separator, key = ref.partition(":")
            if not separator or not key:
                output.append(ActorDisplay(ref, ActorDisplayKind.UNKNOWN, "Unknown", None, False))
            elif kind == "system":
                output.append(ActorDisplay(ref, ActorDisplayKind.SYSTEM, "Telescreen", None, True))
            elif kind == "member":
                entity: DirectoryEntity | None = await self.replica.get(
                    session, DirectoryEntityKind.MEMBER, key
                )
                if entity is None:
                    entity = await self.replica.by_lookup(session, DirectoryEntityKind.MEMBER, key)
                member: MemberEntity | None = (
                    entity.payload
                    if entity is not None and isinstance(entity.payload, MemberEntity)
                    else None
                )
                output.append(
                    ActorDisplay(
                        ref,
                        ActorDisplayKind.MEMBER,
                        member.display_name if member else "Unknown",
                        member.handle if member else None,
                        member.active if member else False,
                    )
                )
            elif kind == "team":
                entity: DirectoryEntity | None = await self.replica.get(
                    session, DirectoryEntityKind.TEAM, key
                )
                if entity is None:
                    entity = await self.replica.by_lookup(session, DirectoryEntityKind.TEAM, key)
                team: TeamEntity | None = (
                    entity.payload
                    if entity is not None and isinstance(entity.payload, TeamEntity)
                    else None
                )
                output.append(
                    ActorDisplay(
                        ref,
                        ActorDisplayKind.TEAM,
                        team.name if team else "Unknown",
                        team.slug if team else None,
                        team is not None,
                    )
                )
            elif kind == "token":
                entity: DirectoryEntity | None = await self.replica.get(
                    session, DirectoryEntityKind.API_TOKEN, key
                )
                token: ApiTokenEntity | None = (
                    entity.payload
                    if entity is not None and isinstance(entity.payload, ApiTokenEntity)
                    else None
                )
                output.append(
                    ActorDisplay(
                        ref,
                        ActorDisplayKind.TOKEN,
                        token.label if token else "Unknown",
                        None,
                        token is not None and not token.revoked,
                    )
                )
            elif kind == "collector":
                entity: DirectoryEntity | None = await self.replica.get(
                    session, DirectoryEntityKind.COLLECTOR, key
                )
                collector: CollectorEntity | None = (
                    entity.payload
                    if entity is not None and isinstance(entity.payload, CollectorEntity)
                    else None
                )
                output.append(
                    ActorDisplay(
                        ref,
                        ActorDisplayKind.SYSTEM,
                        collector.name if collector else "Telescreen",
                        None,
                        True,
                    )
                )
            else:
                output.append(ActorDisplay(ref, ActorDisplayKind.UNKNOWN, "Unknown", None, False))
        return tuple(output)


@dataclass(frozen=True)
class ReplicaOwnerDirectory(OwnerDirectory):
    """Resolve member and team ownership references from replicated identities."""

    replica: DirectoryReplica

    async def member_ref(self, session: AsyncSession, member_id: str) -> str | None:
        entity: DirectoryEntity | None = await self.replica.get(
            session, DirectoryEntityKind.MEMBER, member_id
        )
        if (
            entity is None
            or not isinstance(entity.payload, MemberEntity)
            or not entity.payload.active
        ):
            return None
        return f"member:{entity.payload.handle}"

    async def team_ref(self, session: AsyncSession, team_id: str) -> str | None:
        entity: DirectoryEntity | None = await self.replica.get(
            session, DirectoryEntityKind.TEAM, team_id
        )
        return (
            f"team:{entity.payload.slug}"
            if entity is not None and isinstance(entity.payload, TeamEntity)
            else None
        )

    async def team_refs_of(self, session: AsyncSession, member_id: str) -> tuple[str, ...]:
        member: DirectoryEntity | None = await self.replica.get(
            session, DirectoryEntityKind.MEMBER, member_id
        )
        if (
            member is None
            or not isinstance(member.payload, MemberEntity)
            or not member.payload.active
        ):
            return ()
        entities: ScalarResult[DirectoryEntryRow] = await session.scalars(
            select(self.replica.model).where(
                self.replica.model.kind == DirectoryEntityKind.TEAM.value,
                self.replica.model.present.is_(True),
            )
        )
        teams: list[str] = []
        for row in entities:
            entity: DirectoryEntity = decode_directory_entity(row.payload_json)
            if isinstance(entity.payload, TeamEntity) and member_id in entity.payload.member_ids:
                teams.append(entity.payload.slug)
        return tuple(f"team:{slug}" for slug in sorted(teams))

    async def member_for_owner(self, session: AsyncSession, owner_ref: str) -> str | None:
        kind: str
        separator: str
        key: str
        kind, separator, key = owner_ref.partition(":")
        if not separator or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            return None
        if kind not in tuple(item.value for item in IdentityKind):
            return None
        identity: DirectoryEntity | None = await self.replica.by_lookup(
            session, DirectoryEntityKind.IDENTITY, owner_ref
        )
        if identity is None or not isinstance(identity.payload, IdentityEntity):
            return None
        return await self.member_ref(session, identity.payload.member_id)

    async def team_lead(self, session: AsyncSession, team_slug: str) -> str | None:
        team_entity: DirectoryEntity | None = await self.replica.by_lookup(
            session, DirectoryEntityKind.TEAM, team_slug
        )
        if (
            team_entity is None
            or not isinstance(team_entity.payload, TeamEntity)
            or team_entity.payload.lead_member_id is None
        ):
            return None
        return await self.member_ref(session, team_entity.payload.lead_member_id)


@dataclass(frozen=True)
class ReplicaMembers:
    """Expose the small member lookup needed by service-local preferences."""

    replica: DirectoryReplica

    async def member(self, session: AsyncSession, member_id: str) -> MemberEntity | None:
        entity: DirectoryEntity | None = await self.replica.get(
            session, DirectoryEntityKind.MEMBER, member_id
        )
        return (
            entity.payload
            if entity is not None and isinstance(entity.payload, MemberEntity)
            else None
        )


def _unknown_recipient() -> TelescreenError:
    return TelescreenError(ErrorCode.RECIPIENT_UNKNOWN, "recipient is unknown")
