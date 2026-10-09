"""Persistence queries for issue lifecycle records."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from telescreen.core.vocabulary import IssueStatus
from telescreen.db.models import CounterRow, FindingRow, IssueHistoryRow, IssueRow


class IssueRepository:
    """Issue, finding, history, and counter queries."""

    async def by_grouping_key(self, session: AsyncSession, key: str) -> IssueRow | None:
        """Find an issue by its stable grouping digest."""
        return await session.scalar(select(IssueRow).where(IssueRow.grouping_key == key))

    async def by_id(self, session: AsyncSession, issue_id: str) -> IssueRow | None:
        """Find an issue by its identifier."""
        return await session.get(IssueRow, issue_id)

    async def next_issue_number(self, session: AsyncSession) -> int:
        """Lock and increment the issue number counter in the caller transaction."""
        counter: CounterRow | None = await session.scalar(
            select(CounterRow).where(CounterRow.name == "issue_number").with_for_update()
        )
        if counter is None:
            counter = CounterRow(name="issue_number", value=0)
            session.add(counter)
            await session.flush()
        counter.value += 1
        return counter.value

    async def findings_for_scan(
        self, session: AsyncSession, issue_id: str, scan_job_id: str | None
    ) -> Sequence[FindingRow]:
        """Return existing observations for one issue and scan job."""
        query: Select[tuple[FindingRow]] = select(FindingRow).where(FindingRow.issue_id == issue_id)
        if scan_job_id is None:
            query = query.where(FindingRow.scan_job_id.is_(None))
        else:
            query = query.where(FindingRow.scan_job_id == scan_job_id)
        return (await session.scalars(query)).all()

    async def distinct_fingerprints(self, session: AsyncSession, issue_id: str) -> int:
        """Count the distinct fingerprints recorded for an issue."""
        count: int | None = await session.scalar(
            select(func.count(distinct(FindingRow.fingerprint))).where(
                FindingRow.issue_id == issue_id
            )
        )
        return count or 0

    async def resource_issues(self, session: AsyncSession, resource_key: str) -> Sequence[IssueRow]:
        """Return every issue associated with a resource key."""
        return (
            await session.scalars(select(IssueRow).where(IssueRow.resource_key == resource_key))
        ).all()

    async def open_for_resource(
        self, session: AsyncSession, resource_key: str
    ) -> Sequence[IssueRow]:
        """Return lifecycle states eligible for automatic resolution."""
        open_statuses: tuple[str, ...] = tuple(
            status.value
            for status in (
                IssueStatus.UNRESOLVED,
                IssueStatus.ACKNOWLEDGED,
                IssueStatus.IN_PROGRESS,
                IssueStatus.REGRESSED,
            )
        )
        return (
            await session.scalars(
                select(IssueRow).where(
                    IssueRow.resource_key == resource_key, IssueRow.status.in_(open_statuses)
                )
            )
        ).all()

    async def history(self, session: AsyncSession, issue_id: str) -> Sequence[IssueHistoryRow]:
        """Return issue history in chronological order."""
        return (
            await session.scalars(
                select(IssueHistoryRow)
                .where(IssueHistoryRow.issue_id == issue_id)
                .order_by(IssueHistoryRow.at, IssueHistoryRow.id)
            )
        ).all()

    async def findings(self, session: AsyncSession, issue_id: str) -> Sequence[FindingRow]:
        """Return issue findings in reverse observation order."""
        return (
            await session.scalars(
                select(FindingRow)
                .where(FindingRow.issue_id == issue_id)
                .order_by(FindingRow.observed_at.desc(), FindingRow.id.desc())
            )
        ).all()

    async def unchecked_open(
        self, session: AsyncSession, collector_config_id: str, checked_before: datetime
    ) -> Sequence[IssueRow]:
        """Find sweep-covered open issues older than a reconciliation watermark."""
        open_statuses: tuple[str, ...] = tuple(
            status.value
            for status in (
                IssueStatus.UNRESOLVED,
                IssueStatus.ACKNOWLEDGED,
                IssueStatus.IN_PROGRESS,
                IssueStatus.REGRESSED,
            )
        )
        return (
            await session.scalars(
                select(IssueRow).where(
                    IssueRow.collector_config_id == collector_config_id,
                    IssueRow.sweep_covered.is_(True),
                    IssueRow.last_checked_at < checked_before,
                    IssueRow.status.in_(open_statuses),
                )
            )
        ).all()
