"""Per-member notification preference reads and updates."""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import (
    ErrorCode,
    NotificationCategory,
    NotificationPreference,
)
from telescreen.db.models import MemberRow, NotificationSettingRow


@dataclass(frozen=True)
class CategorySetting:
    """Stored and effective notification preference for one category."""

    category: NotificationCategory
    stored: NotificationPreference | None
    effective: NotificationPreference


@dataclass(frozen=True)
class MemberContact:
    """Contact channels required to resolve notification preferences."""

    id: str
    email: bool
    slack_user_id: bool


class PreferenceMembers(Protocol):
    """Look up contact-channel availability for one member."""

    async def member(self, session: AsyncSession, member_id: str) -> MemberContact | None:
        """Return the member contact DTO, if present."""
        ...


@dataclass(frozen=True)
class PreferenceRepository:
    """Load and persist notification settings inside the caller transaction."""

    async def member(self, session: AsyncSession, member_id: str) -> MemberContact | None:
        """Load contact channels for a member."""
        member: MemberRow | None = await session.get(MemberRow, member_id)
        return (
            MemberContact(member.id, bool(member.email), bool(member.slack_user_id))
            if member is not None
            else None
        )

    async def settings(
        self, session: AsyncSession, member_id: str
    ) -> tuple[NotificationSettingRow, ...]:
        """Return stored settings for one member in category order."""
        rows: tuple[NotificationSettingRow, ...] = tuple(
            (
                await session.scalars(
                    select(NotificationSettingRow)
                    .where(NotificationSettingRow.member_id == member_id)
                    .order_by(NotificationSettingRow.category)
                )
            ).all()
        )
        return rows

    async def get(
        self, session: AsyncSession, member_id: str, category: NotificationCategory
    ) -> NotificationSettingRow | None:
        """Load one category setting."""
        return await session.get(NotificationSettingRow, (member_id, category.value))


@dataclass(frozen=True)
class PreferenceService:
    """Resolve defaults and update a member's notification preferences."""

    repository: PreferenceRepository
    clock: Clock
    members: PreferenceMembers | None = None

    async def member_contact(self, session: AsyncSession, member_id: str) -> MemberContact | None:
        """Return member contacts through the configured member directory."""
        source: PreferenceMembers = self.members or self.repository
        return await source.member(session, member_id)

    async def settings(self, session: AsyncSession, member_id: str) -> tuple[CategorySetting, ...]:
        """Return every category with its stored and effective channel."""
        member: MemberContact | None = await self.member_contact(session, member_id)
        if member is None:
            raise TelescreenError(ErrorCode.FORBIDDEN, "member is not available")
        rows: tuple[NotificationSettingRow, ...] = await self.repository.settings(
            session, member_id
        )
        result: list[CategorySetting] = []
        for category in NotificationCategory:
            row: NotificationSettingRow | None = next(
                (setting for setting in rows if setting.category == category.value), None
            )
            stored: NotificationPreference | None = (
                NotificationPreference(row.channel) if row is not None else None
            )
            effective: NotificationPreference = stored or _default(member)
            result.append(CategorySetting(category, stored, effective))
        return tuple(result)

    async def channel_for(
        self, session: AsyncSession, member_id: str, category: NotificationCategory
    ) -> NotificationPreference:
        """Return the explicit preference or contact-based default."""
        member: MemberContact | None = await self.member_contact(session, member_id)
        if member is None:
            return NotificationPreference.OFF
        row: NotificationSettingRow | None = await self.repository.get(session, member_id, category)
        return NotificationPreference(row.channel) if row is not None else _default(member)

    async def stored_for(
        self, session: AsyncSession, member_id: str, category: NotificationCategory
    ) -> NotificationPreference | None:
        """Return only an explicit preference, for filter-only categories."""
        row: NotificationSettingRow | None = await self.repository.get(session, member_id, category)
        return NotificationPreference(row.channel) if row is not None else None

    async def update(
        self,
        session: AsyncSession,
        member_id: str,
        items: tuple[tuple[NotificationCategory, NotificationPreference | None], ...],
    ) -> tuple[CategorySetting, ...]:
        """Upsert supplied settings; null values delete stored overrides."""
        member: MemberContact | None = await self.member_contact(session, member_id)
        if member is None:
            raise TelescreenError(ErrorCode.FORBIDDEN, "member is not available")
        if len({category for category, _ in items}) != len(items):
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "duplicate notification category")
        if any(
            preference is NotificationPreference.SLACK and not member.slack_user_id
            for _, preference in items
        ):
            raise TelescreenError(
                ErrorCode.RECIPIENT_UNREACHABLE, "notification channel is unavailable"
            )
        for category, preference in items:
            row: NotificationSettingRow | None = await self.repository.get(
                session, member_id, category
            )
            if preference is None:
                if row is not None:
                    await session.delete(row)
            elif row is None:
                session.add(
                    NotificationSettingRow(
                        member_id=member_id,
                        category=category.value,
                        channel=preference.value,
                        updated_at=self.clock.now(),
                    )
                )
            else:
                row.channel = preference.value
                row.updated_at = self.clock.now()
        await session.flush()
        return await self.settings(session, member_id)


def _default(member: MemberContact) -> NotificationPreference:
    """Choose the default personal channel from available member contacts."""
    if member.email:
        return NotificationPreference.EMAIL
    if member.slack_user_id:
        return NotificationPreference.SLACK
    return NotificationPreference.OFF
