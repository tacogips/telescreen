"""Unit coverage for the v1.3 issue query language and compiler."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import (
    UNRESOLVED_STATUSES,
    ErrorCode,
    IssueStatus,
    RuleClass,
    Severity,
)
from telescreen.db.models import FindingRow, IssueRow
from telescreen.issues.query.filters import QueryContext, build_filters
from telescreen.issues.query.model import (
    Comparison,
    ParsedQuery,
    QueryError,
    QueryErrorReason,
    QueryKey,
    TrendBucket,
)
from telescreen.issues.query.parser import parse_issue_query
from telescreen.issues.query.period import Period, parse_period
from telescreen.issues.query.trend import trends
from telescreen.ports.directory import AssigneeIds, ResolvedRecipients
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass
class CountingResolver:
    """Resolver fake used to prove assigned:none bypasses directory lookup."""

    calls: int = 0

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients:
        """Return an empty resolved recipient set."""
        del session, ref
        return ResolvedRecipients((), (), (), ())

    async def is_member_of(self, session: AsyncSession, member_id: str, ref: str) -> bool:
        """Return false for membership checks."""
        del session, member_id, ref
        return False

    async def assignee_of(self, session: AsyncSession, ref: str) -> AssigneeIds:
        """Count calls and return a fixed member assignment."""
        del session, ref
        self.calls += 1
        return AssigneeIds("member-one", None)


def test_parser_supports_lists_quotes_comparisons_and_negation() -> None:
    """Parse accepted structured syntax and preserve B5 negation in the AST."""
    parsed: ParsedQuery = parse_issue_query(
        '!source:slack severity:[high,critical] times_seen:>=5 "two words"'
    )
    assert len(parsed.terms) == 3
    assert parsed.terms[0].key is QueryKey.SOURCE
    assert parsed.terms[0].negated
    assert parsed.terms[1].values == ("high", "critical")
    assert parsed.terms[2].comparison is Comparison.GTE
    assert parsed.terms[2].values == ("5",)
    assert parsed.text == ("two words",)


def test_parser_reports_safe_unknown_key_and_token_limit() -> None:
    """Reject unknown keys and excessive tokens without retaining the query text."""
    with pytest.raises(QueryError) as unknown:
        parse_issue_query("foo:sensitive-query")
    assert unknown.value.token_index == 0
    assert unknown.value.reason is QueryErrorReason.UNKNOWN_KEY
    assert "sensitive-query" not in str(unknown.value)
    with pytest.raises(QueryError) as too_many:
        parse_issue_query(" ".join("term" for _ in range(21)))
    assert too_many.value.reason is QueryErrorReason.TOO_MANY_TOKENS


def test_parser_checks_enum_date_and_numeric_values() -> None:
    """Validate enum values and accept the documented date and number forms."""
    parsed: ParsedQuery = parse_issue_query(
        "status:regressed first_seen:-24h last_seen:+7d values:>2"
    )
    assert len(parsed.terms) == 4
    assert parsed.terms[2].values == ("+7d",)
    assert parsed.terms[3].comparison is Comparison.GT
    with pytest.raises(QueryError) as invalid:
        parse_issue_query("severity:informational")
    assert invalid.value.reason is QueryErrorReason.INVALID_VALUE
    with pytest.raises(QueryError) as too_long:
        parse_issue_query("x" * 1001)
    assert too_long.value.reason is QueryErrorReason.TOO_LONG


def test_parse_period_bounds_custom_and_relative_spans() -> None:
    """Bound custom periods and resolve relative periods against an injected clock."""
    now: datetime = datetime(2026, 10, 12, tzinfo=UTC)
    relative: Period = parse_period("7d", None, None, now)
    assert relative.start == now - timedelta(days=7)
    assert relative.end == now
    with pytest.raises(TelescreenError) as overlong:
        parse_period("custom", now - timedelta(days=400), now, now)
    assert overlong.value.code is ErrorCode.VALIDATION_ERROR
    with pytest.raises(TelescreenError):
        parse_period("custom", now, now - timedelta(days=1), now)


@pytest.mark.asyncio
async def test_b2_assigned_none_matches_both_null_ids_without_resolver() -> None:
    """B2 regression: assigned:none matches only rows with both assignee IDs null."""
    harness: SqliteHarness = await make_sqlite_harness()
    resolver: CountingResolver = CountingResolver()
    now: datetime = datetime(2026, 10, 12, tzinfo=UTC)
    try:
        async with harness.sessions.begin() as session:
            session.add(_issue("unassigned", 1, IssueStatus.UNRESOLVED.value, now))
            assigned: IssueRow = _issue("assigned", 2, IssueStatus.UNRESOLVED.value, now)
            assigned.assignee_team_id = "team-one"
            session.add(assigned)
        async with harness.sessions() as session:
            parsed: ParsedQuery = parse_issue_query("assigned:none")
            context: QueryContext = QueryContext(None, resolver, None, now)
            filters: tuple[ColumnElement[bool], ...] = await build_filters(session, parsed, context)
            matched: list[str] = list(
                (await session.scalars(select(IssueRow.id).where(*filters))).all()
            )
        assert matched == ["unassigned"]
        assert resolver.calls == 0
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_b3_is_unresolved_includes_regressed_acknowledged_in_progress_and_escalating() -> (
    None
):
    """B3 regression: is:unresolved must include the complete open status set."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 12, tzinfo=UTC)
    resolver: CountingResolver = CountingResolver()
    try:
        included: tuple[IssueStatus, ...] = (
            IssueStatus.REGRESSED,
            IssueStatus.ACKNOWLEDGED,
            IssueStatus.IN_PROGRESS,
            IssueStatus.ESCALATING,
        )
        async with harness.sessions.begin() as session:
            for index, status in enumerate(included):
                session.add(_issue(f"open-{index}", index + 1, status.value, now))
            for index, status in enumerate(
                (IssueStatus.ARCHIVED, IssueStatus.RESOLVED, IssueStatus.PENDING_VERIFICATION)
            ):
                session.add(_issue(f"closed-{index}", index + 10, status.value, now))
        async with harness.sessions() as session:
            filters: tuple[ColumnElement[bool], ...] = await build_filters(
                session,
                parse_issue_query("is:unresolved"),
                QueryContext(None, resolver, None, now),
            )
            matched: set[str] = set(
                (await session.scalars(select(IssueRow.status).where(*filters))).all()
            )
        assert matched == {status.value for status in included}
        assert IssueStatus.UNRESOLVED in UNRESOLVED_STATUSES
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_b5_negated_source_excludes_only_the_selected_source() -> None:
    """B5 regression: !source:slack must compile as SQL negation."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 12, tzinfo=UTC)
    resolver: CountingResolver = CountingResolver()
    try:
        async with harness.sessions.begin() as session:
            slack: IssueRow = _issue("slack", 1, IssueStatus.UNRESOLVED.value, now)
            slack.source = "slack"
            other: IssueRow = _issue("other", 2, IssueStatus.UNRESOLVED.value, now)
            other.source = "github"
            session.add_all((slack, other))
        async with harness.sessions() as session:
            filters: tuple[ColumnElement[bool], ...] = await build_filters(
                session,
                parse_issue_query("!source:slack"),
                QueryContext(None, resolver, None, now),
            )
            matched: list[str] = list(
                (await session.scalars(select(IssueRow.id).where(*filters))).all()
            )
        assert matched == ["other"]
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_design_5_1_free_text_matches_mixed_case_on_sqlite() -> None:
    """Design 5.1 regression: free-text search matches regardless of letter case."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 12, tzinfo=UTC)
    resolver: CountingResolver = CountingResolver()
    try:
        async with harness.sessions.begin() as session:
            match: IssueRow = _issue("mixed-case", 1, IssueStatus.UNRESOLVED.value, now)
            match.display_title = "AWS Access Key 100%_safe"
            non_match: IssueRow = _issue("non-match", 2, IssueStatus.UNRESOLVED.value, now)
            non_match.display_title = "Different issue 100X_safe"
            session.add_all((match, non_match))
        async with harness.sessions() as session:
            for query in ("aws", "ACCESS", "100%_safe"):
                filters: tuple[ColumnElement[bool], ...] = await build_filters(
                    session,
                    parse_issue_query(query),
                    QueryContext(None, resolver, None, now),
                )
                matched: list[str] = list(
                    (await session.scalars(select(IssueRow.id).where(*filters))).all()
                )
                assert matched == ["mixed-case"]
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_design_5_1_free_text_compiles_to_postgresql_ilike() -> None:
    """Design 5.1 regression: PostgreSQL free-text predicates use ILIKE."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 12, tzinfo=UTC)
    try:
        async with harness.sessions() as session:
            filters: tuple[ColumnElement[bool], ...] = await build_filters(
                session,
                parse_issue_query("aws"),
                QueryContext(None, CountingResolver(), None, now),
            )
        compiled_sql: str = str(
            select(IssueRow.id).where(*filters).compile(dialect=postgresql.dialect())
        )
        assert "ILIKE" in compiled_sql
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_trend_counts_merged_children_and_zero_fills() -> None:
    """Aggregate child findings into the primary issue's 14-day buckets."""
    harness: SqliteHarness = await make_sqlite_harness()
    now: datetime = datetime(2026, 10, 12, 12, tzinfo=UTC)
    try:
        async with harness.sessions.begin() as session:
            primary: IssueRow = _issue("primary", 1, IssueStatus.UNRESOLVED.value, now)
            child: IssueRow = _issue("child", 2, IssueStatus.UNRESOLVED.value, now)
            child.merged_into_id = primary.id
            session.add_all((primary, child))
            session.add_all(
                (
                    _finding("finding-primary", primary.id, now - timedelta(days=1)),
                    _finding("finding-child", child.id, now - timedelta(days=1)),
                )
            )
        async with harness.sessions() as session:
            result: tuple[tuple[str, tuple[int, ...]], ...] = await trends(
                session, ("primary",), TrendBucket.DAILY_14, now
            )
        assert len(result) == 1
        assert sum(result[0][1]) == 2
        assert result[0][1][0] == 0
    finally:
        await harness.close()


def _issue(issue_id: str, number: int, status: str, now: datetime) -> IssueRow:
    """Build a minimum valid issue database row."""
    return IssueRow(
        id=issue_id,
        number=number,
        grouping_key=f"group-{issue_id}",
        source="custom:test",
        resource_key="resource-key",
        display_locator="locator",
        display_title="safe title",
        rule_class=RuleClass.TOKEN.value,
        severity=Severity.HIGH.value,
        status=status,
        first_seen=now,
        last_seen=now,
        last_checked_at=now,
        created_at=now,
        updated_at=now,
    )


def _finding(finding_id: str, issue_id: str, observed_at: datetime) -> FindingRow:
    """Build one minimum valid finding observation row."""
    return FindingRow(
        id=finding_id,
        issue_id=issue_id,
        scan_job_id=None,
        source="custom:test",
        resource_key="resource-key",
        display_locator="locator",
        location="segment:1",
        rule_id="test.token",
        rule_class=RuleClass.TOKEN.value,
        detector="rule",
        confidence=1.0,
        masked_snippet="[REDACTED]",
        fingerprint=f"fingerprint-{finding_id}",
        fingerprint_key_id="test-key",
        observed_at=observed_at,
    )
