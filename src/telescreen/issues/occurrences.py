"""Read linked issue occurrences by keyed finding fingerprint."""

from dataclasses import dataclass

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.vocabulary import CREDENTIAL_CLASSES, IssueStatus, Severity, Visibility
from telescreen.db.models import FindingRow, IssueRow
from telescreen.issues.keys import short_id


@dataclass(frozen=True)
class LinkedIssue:
    """Safe summary of another unresolved occurrence of the same fingerprint."""

    issue_id: str
    short_id: str
    source: str
    display_locator: str
    status: IssueStatus
    severity: Severity
    exposure_visibility: Visibility


@dataclass(frozen=True)
class OccurrenceResult:
    """Linked occurrences and fingerprint key versions involved in the query."""

    items: tuple[LinkedIssue, ...]
    key_versions: tuple[int, ...]


async def linked(session: AsyncSession, issue_id: str, limit: int = 100) -> OccurrenceResult:
    """Return non-resolved credential issues sharing exact versioned fingerprints."""
    issue: IssueRow | None = await session.get(IssueRow, issue_id)
    if issue is None or issue.rule_class not in tuple(item.value for item in CREDENTIAL_CLASSES):
        return OccurrenceResult((), ())
    own_rows: tuple[FindingRow, ...] = tuple(
        (await session.scalars(select(FindingRow).where(FindingRow.issue_id == issue_id))).all()
    )
    pairs: tuple[tuple[int, str], ...] = tuple(
        sorted({(row.fingerprint_key_version, row.fingerprint) for row in own_rows})
    )
    versions: tuple[int, ...] = tuple(sorted({version for version, _ in pairs}))
    if not pairs:
        return OccurrenceResult((), versions)
    safe_limit: int = max(0, min(limit, 100))
    rows: tuple[IssueRow, ...] = tuple(
        (
            await session.scalars(
                select(IssueRow)
                .join(FindingRow, FindingRow.issue_id == IssueRow.id)
                .where(
                    IssueRow.id != issue_id,
                    IssueRow.status != IssueStatus.RESOLVED.value,
                    tuple_(FindingRow.fingerprint_key_version, FindingRow.fingerprint).in_(pairs),
                )
                .order_by(IssueRow.number)
                .limit(safe_limit)
            )
        )
        .unique()
        .all()
    )
    items: tuple[LinkedIssue, ...] = tuple(
        LinkedIssue(
            row.id,
            short_id(row.number),
            row.source,
            row.display_locator,
            IssueStatus(row.status),
            Severity(row.severity),
            Visibility(row.exposure_visibility),
        )
        for row in rows
    )
    return OccurrenceResult(items, versions)
