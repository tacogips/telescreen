"""Issue exposure and risk search field behavior tests."""

from datetime import UTC, datetime

import httpx
import pytest

from telescreen.core.json import JsonValue, parse_json
from telescreen.db.models import IssueRow
from telescreen.search.backend import (
    FilterOp,
    IndexName,
    SearchDocument,
    SearchFilter,
    SearchPage,
    SearchQuery,
)
from telescreen.search.documents import issue_document
from telescreen.search.meili import MeiliSearchBackend
from telescreen.search.memory import InMemorySearchBackend


def _issue(issue_id: str, visibility: str, risk_score: int) -> IssueRow:
    """Create a minimal issue with explicit v1.1 search attributes."""
    now: datetime = datetime(2026, 10, 10, tzinfo=UTC)
    return IssueRow(
        id=issue_id,
        number=1,
        grouping_key=issue_id,
        source="github",
        resource_key="resource-key",
        display_locator="masked locator",
        resource_ref_json=None,
        collector_config_id=None,
        display_title="Safe issue title",
        rule_class="credential",
        severity="critical",
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
        exposure_visibility=visibility,
        risk_score=risk_score,
    )


@pytest.mark.asyncio
async def test_issue_documents_and_memory_search_support_exposure_and_risk() -> None:
    """Index exposure visibility and filter/sort issue documents by new fields."""
    lower: IssueRow = _issue("issue-low", "public", 20)
    higher: IssueRow = _issue("issue-high", "public", 90)
    private: IssueRow = _issue("issue-private", "private", 100)
    documents: tuple[SearchDocument, ...] = (
        issue_document(lower, None),
        issue_document(higher, None),
        issue_document(private, None),
    )
    assert documents[0].get("exposure_visibility") == "public"
    assert documents[0].get("risk_score") == 20
    backend: InMemorySearchBackend = InMemorySearchBackend()
    await backend.upsert(IndexName.ISSUES, documents)
    page: SearchPage = await backend.search(
        IndexName.ISSUES,
        SearchQuery(
            "",
            (SearchFilter("exposure_visibility", FilterOp.EQ, "public"),),
            ("risk_score:desc",),
        ),
    )
    assert page.total == 2
    assert tuple(document.id for document in page.hits) == ("issue-high", "issue-low")


@pytest.mark.asyncio
async def test_meili_issues_settings_include_visibility_filter_and_risk_sort() -> None:
    """Push the v1.1 fields into Meilisearch's issues index settings."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Capture index settings requests with successful mock responses."""
        requests.append(request)
        if request.url.path == "/indexes":
            return httpx.Response(201, json={"taskUid": 1})
        return httpx.Response(202, json={"taskUid": 2})

    http: httpx.AsyncClient = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    backend: MeiliSearchBackend = MeiliSearchBackend(http, "http://meili")
    await backend.ensure_indexes()
    issue_settings: httpx.Request = next(
        request for request in requests if request.url.path == "/indexes/issues/settings"
    )
    payload: JsonValue = parse_json(await issue_settings.aread())
    filterable: JsonValue | None = payload.get("filterableAttributes")
    sortable: JsonValue | None = payload.get("sortableAttributes")
    assert filterable is not None
    assert sortable is not None
    assert "exposure_visibility" in tuple(item.str_value for item in filterable.items)
    assert "risk_score" in tuple(item.str_value for item in sortable.items)
    await http.aclose()
