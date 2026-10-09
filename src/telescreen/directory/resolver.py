"""Recipient and assignment resolution backed by directory records."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode
from telescreen.db.models import MemberRow, TeamRow
from telescreen.directory.repository import DirectoryRepository
from telescreen.ports.directory import AssigneeIds, ResolvedRecipients


@dataclass
class DirectoryRecipientResolver:
    """Resolve member, team, email and Slack recipient references."""

    repository: DirectoryRepository = field(default_factory=DirectoryRepository)

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients:
        """Resolve a recipient reference or raise a static recipient error."""
        parts: tuple[str, str, str] = ref.partition(":")
        kind: str = parts[0]
        separator: str = parts[1]
        value: str = parts[2]
        if not separator or not value:
            raise TelescreenError(ErrorCode.RECIPIENT_UNKNOWN, "recipient is unknown")
        if kind == "email":
            return ResolvedRecipients((value,), (), (), ())
        if kind == "slack":
            return ResolvedRecipients((), (value,), (), ())
        if kind == "member":
            member: MemberRow | None = await self.repository.member_by_handle(session, value)
            if member is None or not member.active:
                raise TelescreenError(ErrorCode.RECIPIENT_UNKNOWN, "recipient is unknown")
            emails: tuple[str, ...] = (member.email,) if member.email is not None else ()
            slack_ids: tuple[str, ...] = (
                (member.slack_user_id,) if member.slack_user_id is not None else ()
            )
            return ResolvedRecipients(emails, (), slack_ids, (member.id,))
        if kind == "team":
            team: TeamRow | None = await self.repository.team_by_slug(session, value)
            if team is None:
                raise TelescreenError(ErrorCode.RECIPIENT_UNKNOWN, "recipient is unknown")
            members: Sequence[MemberRow] = await self.repository.members_for_team(session, team.id)
            emails: list[str] = [member.email for member in members if member.email is not None]
            if team.email is not None:
                emails.append(team.email)
            channels: tuple[str, ...] = (team.slack_channel,) if team.slack_channel else ()
            slack_ids: tuple[str, ...] = tuple(
                member.slack_user_id for member in members if member.slack_user_id is not None
            )
            return ResolvedRecipients(
                tuple(dict.fromkeys(emails)),
                channels,
                slack_ids,
                tuple(member.id for member in members),
            )
        raise TelescreenError(ErrorCode.RECIPIENT_UNKNOWN, "recipient is unknown")

    async def is_member_of(self, session: AsyncSession, member_id: str, ref: str) -> bool:
        """Check direct membership or membership in a team reference."""
        parts: tuple[str, str, str] = ref.partition(":")
        kind: str = parts[0]
        separator: str = parts[1]
        value: str = parts[2]
        if not separator:
            return False
        if kind == "member":
            member: MemberRow | None = await self.repository.member_by_handle(session, value)
            return member is not None and member.active and member.id == member_id
        if kind == "team":
            team: TeamRow | None = await self.repository.team_by_slug(session, value)
            if team is None:
                return False
            members: Sequence[MemberRow] = await self.repository.members_for_team(session, team.id)
            return any(member.id == member_id for member in members)
        return False

    async def assignee_of(self, session: AsyncSession, ref: str) -> AssigneeIds:
        """Resolve a member or team reference to its database identifier."""
        parts: tuple[str, str, str] = ref.partition(":")
        kind: str = parts[0]
        separator: str = parts[1]
        value: str = parts[2]
        if separator and kind == "member":
            member: MemberRow | None = await self.repository.member_by_handle(session, value)
            if member is not None and member.active:
                return AssigneeIds(member.id, None)
        elif separator and kind == "team":
            team: TeamRow | None = await self.repository.team_by_slug(session, value)
            if team is not None:
                return AssigneeIds(None, team.id)
        raise TelescreenError(ErrorCode.RECIPIENT_UNKNOWN, "recipient is unknown")
