"""Count-only persistence and queries for collector scan coverage."""

import base64
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, Select, and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import ensure_utc
from telescreen.core.ids import new_id
from telescreen.core.vocabulary import CoverageState, Visibility
from telescreen.db.models import (
    ResourceCoverageRow,
    ScanCoverageRow,
    ScanJobRow,
)
from telescreen.ports.collectors import Coverage, ResourceContent
from telescreen.ports.findings import RecordOutcome


@dataclass(frozen=True)
class CoverageSummary:
    """Aggregated coverage counts without resource identifiers."""

    counts: tuple[tuple[str, str, int], ...]
    unscannable_total: int


@dataclass(frozen=True)
class CoverageResourceView:
    """Safe view of a resource's latest incomplete scan."""

    id: str
    resource_key: str
    source: str
    state: str
    reason: str | None
    truncated_bytes: int
    exposure_visibility: str
    first_seen_at: datetime
    last_attempt_at: datetime


@dataclass(frozen=True)
class CoverageRecorder:
    """Persist aggregate and per-resource scan coverage."""

    async def record(
        self,
        session: AsyncSession,
        config_id: str,
        scan_job_id: str | None,
        source: str,
        resource_key: str,
        coverage: Coverage,
        visibility: Visibility,
        now: datetime,
    ) -> None:
        """Increment the job category and maintain the latest unscannable state."""
        reason: str = coverage.reason.value if coverage.reason is not None else ""
        if scan_job_id is not None:
            count_row: ScanCoverageRow | None = await session.get(
                ScanCoverageRow, (scan_job_id, coverage.state.value, reason)
            )
            if count_row is None:
                session.add(
                    ScanCoverageRow(
                        scan_job_id=scan_job_id,
                        state=coverage.state.value,
                        reason=reason,
                        count=1,
                    )
                )
            else:
                count_row.count += 1
        existing: ResourceCoverageRow | None = await session.scalar(
            select(ResourceCoverageRow).where(
                ResourceCoverageRow.collector_config_id == config_id,
                ResourceCoverageRow.resource_key == resource_key,
            )
        )
        if coverage.state is CoverageState.SCANNED:
            if existing is not None:
                await session.delete(existing)
            return
        if existing is None:
            existing = ResourceCoverageRow(
                id=new_id(),
                collector_config_id=config_id,
                resource_key=resource_key,
                source=source,
                state=coverage.state.value,
                reason=coverage.reason.value if coverage.reason is not None else None,
                truncated_bytes=coverage.truncated_bytes,
                exposure_visibility=visibility.value,
                first_seen_at=now,
                last_attempt_at=now,
            )
            try:
                async with session.begin_nested():
                    session.add(existing)
                    await session.flush()
            except IntegrityError:
                existing = await session.scalar(
                    select(ResourceCoverageRow).where(
                        ResourceCoverageRow.collector_config_id == config_id,
                        ResourceCoverageRow.resource_key == resource_key,
                    )
                )
                if existing is None:
                    raise
                _update_resource(existing, coverage, source, visibility, now)
        else:
            _update_resource(existing, coverage, source, visibility, now)

    async def remove(self, session: AsyncSession, config_id: str, resource_key: str) -> None:
        """Delete current unscannable coverage for a removed resource."""
        row: ResourceCoverageRow | None = await session.scalar(
            select(ResourceCoverageRow).where(
                ResourceCoverageRow.collector_config_id == config_id,
                ResourceCoverageRow.resource_key == resource_key,
            )
        )
        if row is not None:
            await session.delete(row)

    async def summary(
        self, session: AsyncSession, config_id: str, since: datetime
    ) -> CoverageSummary:
        """Return counts for scan jobs since a timestamp and current unscannable total."""
        rows: list[tuple[str, str, int | None]] = list(
            (
                await session.execute(
                    select(
                        ScanCoverageRow.state,
                        ScanCoverageRow.reason,
                        func.sum(ScanCoverageRow.count),
                    )
                    .join(ScanJobRow, ScanJobRow.id == ScanCoverageRow.scan_job_id)
                    .where(
                        ScanJobRow.collector_config_id == config_id,
                        ScanJobRow.started_at >= since,
                    )
                    .group_by(ScanCoverageRow.state, ScanCoverageRow.reason)
                    .order_by(ScanCoverageRow.state, ScanCoverageRow.reason)
                )
            )
            .tuples()
            .all()
        )
        total: int = int(
            await session.scalar(
                select(func.count(ResourceCoverageRow.id)).where(
                    ResourceCoverageRow.collector_config_id == config_id
                )
            )
            or 0
        )
        return CoverageSummary(
            tuple((state, reason or "", int(count or 0)) for state, reason, count in rows), total
        )

    async def resources(
        self,
        session: AsyncSession,
        config_id: str,
        state: CoverageState | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[tuple[CoverageResourceView, ...], str | None]:
        """List current unscannable resources, prioritizing public exposure."""
        visibility_rank: ColumnElement[int] = case(
            (ResourceCoverageRow.exposure_visibility.in_(("public", "anyone_with_link")), 0),
            else_=1,
        )
        statement: Select[tuple[ResourceCoverageRow]] = select(ResourceCoverageRow).where(
            ResourceCoverageRow.collector_config_id == config_id
        )
        if state is not None:
            statement = statement.where(ResourceCoverageRow.state == state.value)
        if cursor is not None:
            decoded: tuple[int, datetime, str] | None = _decode_cursor(cursor)
            if decoded is not None:
                rank: int
                attempted_at: datetime
                row_id: str
                rank, attempted_at, row_id = decoded
                statement = statement.where(
                    or_(
                        visibility_rank > rank,
                        and_(
                            visibility_rank == rank,
                            or_(
                                ResourceCoverageRow.last_attempt_at < attempted_at,
                                and_(
                                    ResourceCoverageRow.last_attempt_at == attempted_at,
                                    ResourceCoverageRow.id > row_id,
                                ),
                            ),
                        ),
                    )
                )
        statement = statement.order_by(
            visibility_rank,
            ResourceCoverageRow.last_attempt_at.desc(),
            ResourceCoverageRow.id,
        ).limit(max(1, min(limit, 500)) + 1)
        rows: list[ResourceCoverageRow] = list((await session.scalars(statement)).all())
        has_more: bool = len(rows) > max(1, min(limit, 500))
        selected: list[ResourceCoverageRow] = rows[: max(1, min(limit, 500))]
        views: tuple[CoverageResourceView, ...] = tuple(_view(row) for row in selected)
        next_cursor: str | None = None
        if has_more and selected:
            last: ResourceCoverageRow = selected[-1]
            rank: int = 0 if last.exposure_visibility in ("public", "anyone_with_link") else 1
            next_cursor = _encode_cursor(rank, last.last_attempt_at, last.id)
        return views, next_cursor

    async def public_unscannable_count(self, session: AsyncSession, config_id: str) -> int:
        """Count current public or link-accessible unscannable resources."""
        return int(
            await session.scalar(
                select(func.count(ResourceCoverageRow.id)).where(
                    ResourceCoverageRow.collector_config_id == config_id,
                    ResourceCoverageRow.exposure_visibility.in_(
                        (Visibility.PUBLIC.value, Visibility.ANYONE_WITH_LINK.value)
                    ),
                )
            )
            or 0
        )


def coverage_state(content: ResourceContent, outcome: RecordOutcome) -> Coverage:
    """Resolve explicit adapter coverage, scanner skips, and completion state."""
    if outcome.skipped:
        return Coverage(CoverageState.TOO_LARGE)
    if content.coverage is not None:
        return content.coverage
    state: CoverageState = CoverageState.SCANNED if content.complete else CoverageState.PARTIAL
    return Coverage(state)


def _update_resource(
    row: ResourceCoverageRow,
    coverage: Coverage,
    source: str,
    visibility: Visibility,
    now: datetime,
) -> None:
    """Update current state while retaining the row's first-seen timestamp."""
    row.source = source
    row.state = coverage.state.value
    row.reason = coverage.reason.value if coverage.reason is not None else None
    row.truncated_bytes = coverage.truncated_bytes
    row.exposure_visibility = visibility.value
    row.last_attempt_at = now


def _view(row: ResourceCoverageRow) -> CoverageResourceView:
    """Convert a stored row to its safe API projection."""
    return CoverageResourceView(
        row.id,
        row.resource_key,
        row.source,
        row.state,
        row.reason,
        row.truncated_bytes,
        row.exposure_visibility,
        ensure_utc(row.first_seen_at),
        ensure_utc(row.last_attempt_at),
    )


def _encode_cursor(rank: int, attempted_at: datetime, row_id: str) -> str:
    """Encode keyset components without exposing a locator."""
    value: str = f"{rank}|{attempted_at.isoformat()}|{row_id}"
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[int, datetime, str] | None:
    """Decode an opaque resource page cursor."""
    try:
        padded: str = cursor + "=" * (-len(cursor) % 4)
        value: str = base64.urlsafe_b64decode(padded.encode()).decode()
        rank_text: str
        attempted_text: str
        row_id: str
        rank_text, attempted_text, row_id = value.split("|", maxsplit=2)
        return int(rank_text), datetime.fromisoformat(attempted_text), row_id
    except (ValueError, UnicodeDecodeError):
        return None
