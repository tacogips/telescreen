"""Resolve fallback ownership for overdue data assets."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.vocabulary import RECONCILED_STATUSES
from telescreen.db.models import IssueRow, OwnershipRuleRow, TeamRow
from telescreen.issues.ownership import match_rule
from telescreen.ports.directory import OwnerDirectory
from telescreen.ports.inventory import AssetOwnerResolver


@dataclass(frozen=True)
class IssueAssetOwnerResolver(AssetOwnerResolver):
    """Resolve an open issue owner first, then the first matching ownership rule."""

    owners: OwnerDirectory

    async def fallback_owner(
        self, session: AsyncSession, source: str, resource_key: str, display_locator: str
    ) -> str | None:
        """Return an active member ref or rule team ref when one is available."""
        issue: IssueRow | None = await session.scalar(
            select(IssueRow)
            .where(
                IssueRow.resource_key == resource_key,
                IssueRow.status.in_(tuple(status.value for status in RECONCILED_STATUSES)),
                IssueRow.owner_ref.is_not(None),
            )
            .order_by(IssueRow.id)
        )
        if issue is not None and issue.owner_ref is not None:
            member_ref: str | None = await self.owners.member_for_owner(session, issue.owner_ref)
            if member_ref is not None:
                return member_ref
        rules: tuple[OwnershipRuleRow, ...] = tuple(
            (
                await session.scalars(
                    select(OwnershipRuleRow).order_by(
                        OwnershipRuleRow.position, OwnershipRuleRow.id
                    )
                )
            ).all()
        )
        rule: OwnershipRuleRow | None = match_rule(rules, source, display_locator)
        if rule is None:
            return None
        team: TeamRow | None = await session.get(TeamRow, rule.team_id)
        return f"team:{team.slug}" if team is not None else None
