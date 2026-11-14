"""Inventory enrichment must work with physically separate issue tables."""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from telescreen.core.vocabulary import ActorDisplayKind
from telescreen.db.base import Base
from telescreen.db.models import (
    DataAssetRow,
    IssueRow,
)
from telescreen.db.session import create_sessionmaker
from telescreen.inventory.queries import AssetDetailView, AssetFilters, AssetPage, AssetQueries
from telescreen.issues.asset_context import IssueAssetLookup, IssueAssetOwnerLookup
from telescreen.issues.asset_owner import IssueAssetOwnerResolver
from telescreen.ports.directory import ActorDisplay
from telescreen.ports.issues import ResourceIssueRef
from tests.issues.v14_asset_owner.test_resolver import FakeOwnerDirectory, _issue


@dataclass(frozen=True)
class SqlIssueReader:
    """Read issue references from the session supplied by the gateway."""

    async def open_issues_for_resources(
        self, session: AsyncSession, resource_keys: tuple[str, ...]
    ) -> tuple[ResourceIssueRef, ...]:
        """Return only the requested resources in the issue database."""
        rows: tuple[IssueRow, ...] = tuple(
            (
                await session.scalars(
                    select(IssueRow).where(IssueRow.resource_key.in_(resource_keys))
                )
            ).all()
        )
        return tuple(
            ResourceIssueRef(row.resource_key, row.id, f"ISSUE-{row.number}") for row in rows
        )


@dataclass(frozen=True)
class UnusedActors:
    """Resolve no references without requiring identity tables."""

    async def display(self, refs: tuple[str, ...]) -> tuple[ActorDisplay, ...]:
        """Keep unknown actor displays detached from persistence."""
        return tuple(
            ActorDisplay(ref, ActorDisplayKind.UNKNOWN, "Unknown", None, False) for ref in refs
        )


@pytest.mark.asyncio
async def test_inventory_enrichment_uses_separate_issue_database() -> None:
    """List, detail and fallback owner work without issue tables in inventory."""
    from telescreen.core.vocabulary import IssueStatus

    inventory: AsyncEngine = create_async_engine("sqlite+aiosqlite:///:memory:")
    issues: AsyncEngine = create_async_engine("sqlite+aiosqlite:///:memory:")
    now: datetime = datetime(2026, 10, 14, tzinfo=UTC)
    try:
        async with inventory.begin() as connection:
            for table in (
                Base.metadata.tables["data_asset_items"],
                Base.metadata.tables["data_assets"],
                Base.metadata.tables["data_fields"],
                Base.metadata.tables["data_field_values"],
            ):
                await connection.run_sync(table.create)
        async with issues.begin() as connection:
            await connection.run_sync(Base.metadata.tables["issues"].create)
        async with create_sessionmaker(issues).begin() as session:
            session.add(_issue(IssueStatus.UNRESOLVED, "identity:owner", now))
        lookup: IssueAssetLookup = IssueAssetLookup(create_sessionmaker(issues), SqlIssueReader())
        owners: IssueAssetOwnerLookup = IssueAssetOwnerLookup(
            create_sessionmaker(issues), IssueAssetOwnerResolver(FakeOwnerDirectory())
        )
        queries: AssetQueries = AssetQueries(lookup, UnusedActors())
        async with create_sessionmaker(inventory).begin() as session:
            session.add(
                DataAssetRow(
                    id="asset",
                    number=1,
                    resource_key="r" * 64,
                    source="drive",
                    origin="discovered",
                    kind="other",
                    state="present",
                    name="Report",
                    display_locator="/sheets/team/report",
                    first_seen_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            page: AssetPage = await queries.list(session, AssetFilters(), now.date())
            assert page.items[0].open_issue_count == 1
            detail: AssetDetailView | None = await queries.detail(session, "asset", now.date())
            assert detail is not None
            assert detail.open_issues[0].issue_id == "issue-1"
            assert (
                await owners.fallback_owner("drive", "r" * 64, "/sheets/team/report")
                == "member:alice"
            )
            assert await lookup.open_issues_for_resources(("missing",)) == ()
            assert await lookup.open_issues_for_resources(()) == ()
    finally:
        await inventory.dispose()
        await issues.dispose()
