"""Audit actor classification and safe display resolution."""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.vocabulary import ActorDisplayKind, ActorKind
from telescreen.db.models import ApiTokenRow, MemberRow, TeamRow
from telescreen.directory.repository import DirectoryRepository
from telescreen.ports.directory import ActorDirectory, ActorDisplay


def actor_kind_for(actor_ref: str) -> ActorKind:
    """Classify an actor reference by its prefix for audit entries."""
    if actor_ref.startswith("member:"):
        return ActorKind.MEMBER
    if actor_ref.startswith("token:"):
        return ActorKind.TOKEN
    return ActorKind.SYSTEM


@dataclass
class DirectoryActorDirectory(ActorDirectory):
    """Resolve actor references in batches to safe display names."""

    repository: DirectoryRepository = field(default_factory=DirectoryRepository)

    async def display(
        self, session: AsyncSession, refs: tuple[str, ...]
    ) -> tuple[ActorDisplay, ...]:
        """Resolve actor refs while preserving input length and order."""
        member_keys: set[str] = set()
        team_keys: set[str] = set()
        token_keys: set[str] = set()
        parsed: list[tuple[str, str, str]] = []
        kind: str
        separator: str
        key: str
        for ref in refs:
            kind, separator, key = ref.partition(":")
            parsed.append((kind, separator, key))
            if separator and key:
                if kind == "member":
                    member_keys.add(key)
                elif kind == "team":
                    team_keys.add(key)
                elif kind == "token":
                    token_keys.add(key)
        members: tuple[MemberRow, ...] = ()
        teams: tuple[TeamRow, ...] = ()
        tokens: tuple[ApiTokenRow, ...] = ()
        if member_keys:
            members = tuple(
                (
                    await session.scalars(
                        select(MemberRow).where(
                            (MemberRow.id.in_(member_keys)) | (MemberRow.handle.in_(member_keys))
                        )
                    )
                ).all()
            )
        if team_keys:
            teams = tuple(
                (
                    await session.scalars(
                        select(TeamRow).where(
                            (TeamRow.id.in_(team_keys)) | (TeamRow.slug.in_(team_keys))
                        )
                    )
                ).all()
            )
        if token_keys:
            tokens = tuple(
                (
                    await session.scalars(select(ApiTokenRow).where(ApiTokenRow.id.in_(token_keys)))
                ).all()
            )
        resolved: list[ActorDisplay] = []
        for ref, (kind, separator, key) in zip(refs, parsed, strict=True):
            if not separator or not key:
                resolved.append(ActorDisplay(ref, ActorDisplayKind.UNKNOWN, "Unknown", None, False))
            elif kind == "system":
                resolved.append(
                    ActorDisplay(ref, ActorDisplayKind.SYSTEM, "Telescreen", None, True)
                )
            elif kind == "member":
                row: MemberRow | None = next(
                    (item for item in members if item.id == key or item.handle == key), None
                )
                resolved.append(
                    ActorDisplay(
                        ref,
                        ActorDisplayKind.MEMBER,
                        row.display_name if row else "Unknown",
                        row.handle if row else None,
                        row.active if row else False,
                    )
                )
            elif kind == "team":
                row_team: TeamRow | None = next(
                    (item for item in teams if item.id == key or item.slug == key), None
                )
                resolved.append(
                    ActorDisplay(
                        ref,
                        ActorDisplayKind.TEAM,
                        row_team.name if row_team else "Unknown",
                        row_team.slug if row_team else None,
                        row_team is not None,
                    )
                )
            elif kind == "token":
                row_token: ApiTokenRow | None = next(
                    (item for item in tokens if item.id == key), None
                )
                resolved.append(
                    ActorDisplay(
                        ref,
                        ActorDisplayKind.TOKEN,
                        row_token.label if row_token else "Unknown",
                        None,
                        row_token is not None and row_token.revoked_at is None,
                    )
                )
            else:
                resolved.append(ActorDisplay(ref, ActorDisplayKind.UNKNOWN, "Unknown", None, False))
        return tuple(resolved)
