"""Ownership-rule matching over the local directory replica."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.ownership_match import match_rule
from telescreen.ports.directory_projection import (
    DirectoryEntity,
    DirectoryEntityKind,
    OwnershipRuleEntry,
    OwnershipRulesEntity,
    TeamEntity,
)
from telescreen.replica.store import DirectoryReplica


@dataclass(frozen=True)
class ReplicaOwnershipRules:
    """Match an issue source and locator against projected ownership rules."""

    replica: DirectoryReplica

    async def match(self, session: AsyncSession, source: str, locator: str) -> str | None:
        entity: DirectoryEntity | None = await self.replica.get(
            session, DirectoryEntityKind.OWNERSHIP_RULES, "rules"
        )
        if entity is None or not isinstance(entity.payload, OwnershipRulesEntity):
            return None
        rule: OwnershipRuleEntry | None = match_rule(entity.payload.rules, source, locator)
        if rule is None:
            return None
        team: DirectoryEntity | None = await self.replica.get(
            session, DirectoryEntityKind.TEAM, rule.team_id
        )
        return (
            f"team:{team.payload.slug}"
            if team is not None and isinstance(team.payload, TeamEntity)
            else None
        )
