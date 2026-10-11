"""Recipient resolution ports and DTOs."""

import re
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

SLACK_CHANNEL_ID: re.Pattern[str] = re.compile(r"^[CG][A-Z0-9]{8,}$")
"""Public (C) or private (G) Slack channel id; user (U/W) and DM (D) ids are not channels."""


@dataclass(frozen=True)
class ResolvedRecipients:
    """Resolved delivery addresses for a recipient reference."""

    emails: tuple[str, ...]
    slack_channels: tuple[str, ...]
    slack_user_ids: tuple[str, ...]
    member_ids: tuple[str, ...]


@dataclass(frozen=True)
class AssigneeIds:
    """Resolved member or team assignment identifiers."""

    member_id: str | None
    team_id: str | None


class RecipientResolver(Protocol):
    """Resolve and check member, team, and mailing-list references."""

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients:
        """Resolve a recipient reference or raise recipient_unknown."""
        ...

    async def is_member_of(self, session: AsyncSession, member_id: str, ref: str) -> bool:
        """Return whether the member belongs to the referenced member or team."""
        ...

    async def assignee_of(self, session: AsyncSession, ref: str) -> AssigneeIds:
        """Resolve a member or team assignment reference."""
        ...


class OwnerDirectory(Protocol):
    """Resolve keyed owner identities to application member and team refs."""

    async def member_ref(self, session: AsyncSession, member_id: str) -> str | None:
        """Return a stable member reference for an active member."""
        ...

    async def team_ref(self, session: AsyncSession, team_id: str) -> str | None:
        """Return a stable team reference for a configured team."""
        ...

    async def team_refs_of(self, session: AsyncSession, member_id: str) -> tuple[str, ...]:
        """Return team references associated with a member."""
        ...

    async def member_for_owner(self, session: AsyncSession, owner_ref: str) -> str | None:
        """Resolve a keyed owner reference to an active member reference (member:<handle>)."""
        ...

    async def team_lead(self, session: AsyncSession, team_slug: str) -> str | None:
        """Return the lead member reference for a team slug."""
        ...
