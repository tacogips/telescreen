"""Fallback asset owner resolution order and open-issue behavior."""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.vocabulary import IssueStatus
from telescreen.db.models import IssueRow, OwnershipRuleRow, TeamRow
from telescreen.issues.asset_owner import IssueAssetOwnerResolver
from telescreen.ports.directory import OwnerDirectory
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass(frozen=True)
class FakeOwnerDirectory(OwnerDirectory):
    """Translate the known identity reference to an active member handle."""

    async def member_ref(self, session: AsyncSession, member_id: str) -> str | None:
        """Return a member ref for one known id."""
        del session
        return "member:alice" if member_id == "member-id" else None

    async def team_ref(self, session: AsyncSession, team_id: str) -> str | None:
        """Return a team ref for one known id."""
        del session
        return "team:ops" if team_id == "team-id" else None

    async def team_refs_of(self, session: AsyncSession, member_id: str) -> tuple[str, ...]:
        """Return no team memberships."""
        del session, member_id
        return ()

    async def member_for_owner(self, session: AsyncSession, owner_ref: str) -> str | None:
        """Return the normalized handle ref for a known owner identity."""
        del session
        return "member:alice" if owner_ref == "identity:owner" else None

    async def team_lead(self, session: AsyncSession, team_slug: str) -> str | None:
        """Return no lead for this fallback-only test."""
        del session, team_slug
        return None


def _issue(status: IssueStatus, owner_ref: str | None, now: datetime) -> IssueRow:
    """Build an issue that shares the data asset's resource key."""
    return IssueRow(
        id="issue-1",
        number=1,
        grouping_key="g" * 64,
        source="drive",
        resource_key="r" * 64,
        display_locator="/sheets/team/report",
        display_title="Report",
        rule_class="credential",
        severity="high",
        status=status.value,
        first_seen=now,
        last_seen=now,
        last_checked_at=now,
        created_at=now,
        updated_at=now,
        owner_ref=owner_ref,
    )


@pytest.mark.asyncio
async def test_open_issue_owner_precedes_matching_rule() -> None:
    """The normalized open issue owner wins over an enabled ownership rule."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    resolver: IssueAssetOwnerResolver = IssueAssetOwnerResolver(FakeOwnerDirectory())
    try:
        async with harness.sessions.begin() as session:
            session.add(
                TeamRow(
                    id="team-id",
                    slug="ops",
                    name="Operations",
                    email=None,
                    slack_channel=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                OwnershipRuleRow(
                    id="rule-1",
                    position=0,
                    source="drive",
                    pattern="/sheets/**",
                    team_id="team-id",
                    enabled=True,
                    created_by="member:admin",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(_issue(IssueStatus.UNRESOLVED, "identity:owner", now))
            await session.flush()
            owner: str | None = await resolver.fallback_owner(
                session, "drive", "r" * 64, "/sheets/team/report"
            )
            assert owner == "member:alice"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_owner_resolver_uses_first_rule_and_ignores_resolved_issue_owner() -> None:
    """A resolved issue is ignored and the enabled matching rule supplies its team."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 13, tzinfo=UTC)
    resolver: IssueAssetOwnerResolver = IssueAssetOwnerResolver(FakeOwnerDirectory())
    try:
        async with harness.sessions.begin() as session:
            session.add(
                TeamRow(
                    id="team-id",
                    slug="ops",
                    name="Operations",
                    email=None,
                    slack_channel=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                OwnershipRuleRow(
                    id="rule-1",
                    position=0,
                    source="drive",
                    pattern="/sheets/**",
                    team_id="team-id",
                    enabled=True,
                    created_by="member:admin",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(_issue(IssueStatus.RESOLVED, "identity:owner", now))
            await session.flush()
            owner: str | None = await resolver.fallback_owner(
                session, "drive", "r" * 64, "/sheets/team/report"
            )
            assert owner == "team:ops"
    finally:
        await harness.close()
