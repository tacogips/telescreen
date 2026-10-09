"""Recipient resolution ports and DTOs."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


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
