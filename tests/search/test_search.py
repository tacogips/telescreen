"""Guarded search indexing and backend behavior tests."""

import asyncio
import os
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.api.routers.search import SearchHit, _hit
from telescreen.audit.service import AuditLogService
from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, parse_json
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import ErrorCode
from telescreen.db.models import AuditRecordRow, IssueRow
from telescreen.detection import RuleDetector
from telescreen.masking.index_guard import IndexGuard
from telescreen.masking.redact import Redactor
from telescreen.search.backend import (
    FilterOp,
    IndexName,
    SearchDocument,
    SearchFilter,
    SearchPage,
    SearchQuery,
)
from telescreen.search.indexer import DocumentIndexer
from telescreen.search.meili import MeiliSearchBackend
from telescreen.search.memory import InMemorySearchBackend
from telescreen.search.reindex import ReindexCounts, reindex_all
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import Sample, assert_no_leaks, github_token


def _issue(issue_id: str, title: str, now: datetime) -> IssueRow:
    """Create a minimal persisted issue fixture."""
    return IssueRow(
        id=issue_id,
        number=1,
        grouping_key=issue_id,
        source="github",
        resource_key="resource-key",
        display_locator="repo/path",
        resource_ref_json=None,
        collector_config_id=None,
        display_title=title,
        rule_class="credential",
        severity="high",
        status="unresolved",
        assignee_member_id=None,
        assignee_team_id=None,
        first_seen=now,
        last_seen=now,
        last_checked_at=now,
        sweep_covered=False,
        times_seen=1,
        distinct_fingerprints=1,
        regression_count=0,
        resolved_at=None,
        resolution=None,
        created_at=now,
        updated_at=now,
    )


def test_search_hit_mapping_covers_each_index() -> None:
    """The API mapper returns the stable display fields for each index."""
    issue: SearchHit = _hit(
        IndexName.ISSUES,
        SearchDocument(
            "issue-1",
            (
                ("short_id", "TELE-1"),
                ("display_title", "Credential finding"),
                ("display_locator", "repo/file"),
                ("status", "unresolved"),
                ("last_seen_ts", 100),
            ),
        ),
    )
    finding: SearchHit = _hit(
        IndexName.FINDINGS,
        SearchDocument("finding-1", (("rule_id", "github_token"), ("masked_snippet", "ghp_***"))),
    )
    run: SearchHit = _hit(
        IndexName.RUNS,
        SearchDocument("run-1", (("workflow", "Remediate"), ("issue_id", "issue-1"))),
    )
    audit: SearchHit = _hit(
        IndexName.AUDIT,
        SearchDocument("audit-1", (("action", "issue.acknowledged"), ("summary", "Acknowledged"))),
    )
    assert (issue.title, issue.snippet, issue.status, issue.at) == (
        "TELE-1 Credential finding",
        "repo/file",
        "unresolved",
        "100",
    )
    assert (finding.title, finding.snippet) == ("github_token", "ghp_***")
    assert (run.title, run.snippet) == ("Remediate", "issue-1")
    assert (audit.title, audit.snippet) == ("issue.acknowledged", "Acknowledged")


@pytest.mark.asyncio
async def test_index_guard_rejects_sensitive_issue_and_audits_metadata() -> None:
    """A rejected document is not indexed and its audit metadata contains no sample."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))
    audit: AuditLogService = AuditLogService(clock, Redactor(RuleDetector()))
    sample: Sample = github_token()
    async with harness.sessions.begin() as session:
        session.add(_issue("issue-sensitive", sample.value, clock.now()))
    backend: InMemorySearchBackend = InMemorySearchBackend()
    indexer: DocumentIndexer = DocumentIndexer(
        harness.sessions, backend, IndexGuard(RuleDetector()), audit, clock
    )
    await indexer.index_issue("issue-sensitive")
    assert (await backend.search(IndexName.ISSUES, SearchQuery(""))).total == 0
    async with harness.sessions() as session:
        audit_rows: list[AuditRecordRow] = list(
            (await session.scalars(select(AuditRecordRow))).all()
        )
        assert len(audit_rows) == 1
        assert audit_rows[0].action == "index_guard.rejected"
        assert_no_leaks(audit_rows[0].details_json, "index rejection audit")
    await harness.close()


@pytest.mark.asyncio
async def test_clean_issue_is_indexed_searchable_and_reindexed() -> None:
    """Clean display data is indexed, queryable and included in reindex counts."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))
    audit: AuditLogService = AuditLogService(clock, Redactor(RuleDetector()))
    async with harness.sessions.begin() as session:
        session.add(_issue("issue-clean", "Public incident title", clock.now()))
    backend: InMemorySearchBackend = InMemorySearchBackend()
    indexer: DocumentIndexer = DocumentIndexer(
        harness.sessions, backend, IndexGuard(RuleDetector()), audit, clock
    )
    await indexer.index_issue("issue-clean")
    page: SearchPage = await backend.search(
        IndexName.ISSUES,
        SearchQuery("TELE-1", (SearchFilter("status", FilterOp.EQ, "unresolved"),)),
    )
    assert page.total == 1
    assert page.hits[0].get("display_title") == "Public incident title"
    counts: ReindexCounts = await reindex_all(
        harness.sessions, backend, IndexGuard(RuleDetector()), audit
    )
    assert counts.issues == 1
    await harness.close()


@pytest.mark.asyncio
async def test_meili_backend_escapes_filters_and_sends_settings() -> None:
    """Meilisearch settings and filters use canonical JSON and escaped literals."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Return deterministic Meilisearch responses while recording requests."""
        requests.append(request)
        if request.url.path == "/indexes":
            return httpx.Response(409, json={"message": "already exists"})
        if request.url.path.endswith("/search"):
            return httpx.Response(
                200,
                json={
                    "hits": [{"id": "issue-1", "status": "resolved", "display_title": "Safe"}],
                    "estimatedTotalHits": 1,
                },
            )
        return httpx.Response(202, json={"taskUid": 1})

    http: httpx.AsyncClient = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend: MeiliSearchBackend = MeiliSearchBackend(
        http, "http://meili", SecretValue("placeholder")
    )
    await backend.ensure_indexes()
    page: SearchPage = await backend.search(
        IndexName.ISSUES,
        SearchQuery("", (SearchFilter("status", FilterOp.EQ, 'res"olved'),)),
    )
    search_request: httpx.Request = requests[-1]
    body: JsonValue = parse_json(await search_request.aread())
    filters: JsonValue | None = body.get("filter")
    assert filters is not None
    assert filters.items[0].str_value == 'status = "res\\"olved"'
    assert page.total == 1
    assert page.hits[0].get("display_title") == "Safe"
    assert any(request.url.path.endswith("/settings") for request in requests)
    assert requests[1].content and b"filterableAttributes" in requests[1].content
    await http.aclose()


@pytest.mark.asyncio
async def test_meili_upsert_failure_is_absorbed_and_not_logged_with_response_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Indexer absorbs backend errors and logs only safe ids and index names."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))
    audit: AuditLogService = AuditLogService(clock, Redactor(RuleDetector()))
    async with harness.sessions.begin() as session:
        session.add(_issue("issue-clean", "Public incident title", clock.now()))

    def handler(request: httpx.Request) -> httpx.Response:
        """Return an error response containing a sentinel that must not be logged."""
        del request
        return httpx.Response(500, text="provider detail sentinel")

    http: httpx.AsyncClient = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend: MeiliSearchBackend = MeiliSearchBackend(http, "http://meili")
    indexer: DocumentIndexer = DocumentIndexer(
        harness.sessions, backend, IndexGuard(RuleDetector()), audit, clock
    )
    await indexer.index_issue("issue-clean")
    assert "provider detail sentinel" not in caplog.text
    assert "issue-clean" in caplog.text
    await http.aclose()
    await harness.close()


@pytest.mark.asyncio
async def test_indexer_absorbs_session_factory_failure_without_logging_error(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed database load is absorbed and only index/document identifiers are logged."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))
    audit: AuditLogService = AuditLogService(clock, Redactor(RuleDetector()))

    def fail_session(factory: async_sessionmaker[AsyncSession]) -> AsyncSession:
        """Raise a sentinel when the indexer opens its session."""
        del factory
        raise RuntimeError("provider detail sentinel")

    monkeypatch.setattr(async_sessionmaker, "__call__", fail_session)
    indexer: DocumentIndexer = DocumentIndexer(
        harness.sessions, InMemorySearchBackend(), IndexGuard(RuleDetector()), audit, clock
    )
    await indexer.index_issue("issue-load-failure")
    assert "issue-load-failure" in caplog.text
    assert "provider detail sentinel" not in caplog.text
    await harness.close()


@pytest.mark.asyncio
async def test_meili_settings_failure_raises_static_provider_error() -> None:
    """A failed settings PUT raises a static provider error without response text."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Return a settings failure with a sensitive-looking response body."""
        if request.url.path == "/indexes":
            return httpx.Response(409, json={"message": "already exists"})
        return httpx.Response(500, text="provider detail sentinel")

    http: httpx.AsyncClient = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend: MeiliSearchBackend = MeiliSearchBackend(http, "http://meili")
    with pytest.raises(TelescreenError) as error:
        await backend.ensure_indexes()
    assert error.value.code is ErrorCode.PROVIDER_ERROR
    assert error.value.detail == "Meilisearch settings update failed"
    assert "provider detail sentinel" not in str(error.value)
    await http.aclose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_meili_integration_ensure_upsert_search_round_trip() -> None:
    """The configured Meilisearch service accepts and returns an indexed document."""
    base_url: str | None = os.environ.get("TELESCREEN_TEST_MEILI_URL")
    if base_url is None:
        pytest.skip("requires TELESCREEN_TEST_MEILI_URL")
    http: httpx.AsyncClient = httpx.AsyncClient(timeout=10.0)
    backend: MeiliSearchBackend = MeiliSearchBackend(http, base_url)
    document: SearchDocument = SearchDocument(
        "p15-integration-round-trip", (("display_title", "p15 integration marker"),)
    )
    indexed: bool = False
    try:
        await backend.ensure_indexes()
        await backend.upsert(IndexName.ISSUES, (document,))
        indexed = True
        page: SearchPage = SearchPage((), 0)
        for _ in range(25):
            page = await backend.search(IndexName.ISSUES, SearchQuery("p15 integration marker"))
            if page.total:
                break
            await asyncio.sleep(0.2)
        assert page.total == 1
        assert page.hits[0].id == document.id
    finally:
        if indexed:
            await backend.delete(IndexName.ISSUES, (document.id,))
        await http.aclose()
