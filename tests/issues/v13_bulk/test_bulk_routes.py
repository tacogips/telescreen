"""HTTP regressions for the v1.3 bulk issue router contract."""

from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from telescreen.api.common import ApiContext, install_error_handlers
from telescreen.api.routers.issue_bulk import IssueBulkRouterDeps, build_issue_bulk_router
from telescreen.core.clock import FakeClock
from telescreen.core.vocabulary import ApiScope, IssueStatus
from telescreen.db.models import IssueRow
from telescreen.directory import CreatedToken, DirectoryRecipientResolver, TokenService
from telescreen.issues.archive import ArchivePolicy
from telescreen.issues.bulk import BulkService
from telescreen.issues.lifecycle import IssueRemediationService
from telescreen.issues.merge import MergeService
from telescreen.issues.query.filters import QueryContext
from telescreen.issues.query.period import Period, parse_period
from telescreen.issues.query.select import MatchResult, select_matching_ids
from telescreen.issues.service import IssueService
from tests.issues.v13_lifecycle.test_archive import (
    NOW,
    RecordingActivity,
    RecordingAudit,
    RecordingEvents,
    RecordingIndexer,
    _issue,
    _world,
)
from tests.issues.v13_lifecycle.test_archive_exits import _services
from tests.support.db import SqliteHarness


def _bulk_app(
    db: SqliteHarness,
    audit: RecordingAudit,
    events: RecordingEvents,
    activity: RecordingActivity,
) -> tuple[FastAPI, DirectoryRecipientResolver]:
    """Build the unregistered bulk router in an HTTP test app."""
    clock: FakeClock = FakeClock(NOW)
    issue_service: IssueService
    remediation: IssueRemediationService
    issue_service, _, remediation = _services(audit, events, activity)
    indexer: RecordingIndexer = RecordingIndexer([])
    bulk: BulkService = BulkService(
        db.sessions,
        issue_service,
        remediation,
        ArchivePolicy(),
        clock,
        audit,
        indexer,
    )
    merge: MergeService = MergeService(db.sessions, audit, clock, indexer, activity)
    resolver: DirectoryRecipientResolver = DirectoryRecipientResolver()
    ctx: ApiContext = ApiContext(db.sessions, clock, audit)
    app: FastAPI = FastAPI()
    install_error_handlers(app)
    app.include_router(
        build_issue_bulk_router(IssueBulkRouterDeps(ctx, bulk, merge, indexer, resolver))
    )
    return app, resolver


@pytest.mark.asyncio
async def test_int_w2_04_router_custom_period_matches_selector_and_rejects_invalid_dates() -> None:
    """INT-W2-04: HTTP bulk dates match the list selector and reject invalid custom periods."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    app: FastAPI
    resolver: DirectoryRecipientResolver
    app, resolver = _bulk_app(db, audit, events, activity)
    inside: IssueRow = _issue("http-custom-in", 70)
    inside.source = "slack"
    inside.needs_review = True
    outside: IssueRow = _issue("http-custom-out", 71)
    outside.source = "slack"
    outside.first_seen = outside.last_seen = NOW - timedelta(days=20)
    outside.needs_review = True
    clock: FakeClock = FakeClock(NOW)
    token: CreatedToken
    async with db.sessions.begin() as session:
        session.add_all((inside, outside))
        token = await TokenService(audit, clock).create(
            session, "bulk-write", frozenset((ApiScope.WRITE,)), None
        )

    start: str = (NOW - timedelta(days=12)).isoformat()
    end: str = NOW.isoformat()
    selected_period: Period = parse_period("custom", NOW - timedelta(days=12), NOW, NOW)
    query_context: QueryContext = QueryContext(None, resolver, None, NOW)
    expected: MatchResult
    async with db.sessions() as session:
        expected = await select_matching_ids(
            session, "source:slack", selected_period, query_context, 1000
        )
    assert expected.ids == (inside.id,)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response: Response = await client.post(
            "/api/issues/bulk",
            headers={"Authorization": f"Bearer {token.token}"},
            json={
                "query": "source:slack",
                "period": "custom",
                "start": start,
                "end": end,
                "action": "mark_reviewed",
            },
        )
        assert response.status_code == 200
        assert f'"succeeded":["{expected.ids[0]}"]'.encode() in response.content

        issue_bulk_count: int = sum(entry.action == "issue.bulk" for entry in audit.entries)
        assert issue_bulk_count == 1
        invalid_responses: tuple[Response, ...] = (
            await client.post(
                "/api/issues/bulk",
                headers={"Authorization": f"Bearer {token.token}"},
                json={
                    "query": "source:slack",
                    "period": "custom",
                    "action": "mark_reviewed",
                },
            ),
            await client.post(
                "/api/issues/bulk",
                headers={"Authorization": f"Bearer {token.token}"},
                json={
                    "query": "source:slack",
                    "period": "custom",
                    "start": (NOW - timedelta(days=366)).isoformat(),
                    "end": end,
                    "action": "mark_reviewed",
                },
            ),
            await client.post(
                "/api/issues/bulk",
                headers={"Authorization": f"Bearer {token.token}"},
                json={
                    "ids": [inside.id],
                    "action": "archive",
                    "params": {"archive": {"duration_minutes": 60}},
                },
            ),
            await client.post(
                "/api/issues/bulk",
                headers={"Authorization": f"Bearer {token.token}"},
                json={
                    "ids": [inside.id],
                    "action": "archive",
                    "params": {
                        "archive": {
                            "kind": "until_escalating",
                            "duration_minutes": 60,
                        }
                    },
                },
            ),
        )

    assert all(item.status_code == 422 for item in invalid_responses)
    assert all(b'"code":"validation_error"' in item.content for item in invalid_responses)
    assert sum(entry.action == "issue.bulk" for entry in audit.entries) == issue_bulk_count
    async with db.sessions() as session:
        stored_inside: IssueRow | None = await session.get(IssueRow, inside.id)
        stored_outside: IssueRow | None = await session.get(IssueRow, outside.id)
    assert stored_inside is not None
    assert stored_inside.needs_review is False
    assert stored_inside.status == IssueStatus.UNRESOLVED.value
    assert stored_inside.archive_kind is None and stored_inside.archive_until is None
    assert stored_outside is not None and stored_outside.needs_review is True
    await db.close()


@pytest.mark.asyncio
async def test_read_only_token_cannot_use_bulk_merge_or_unmerge_routes() -> None:
    """A read-only token gets 403 on all bulk routes and leaves issues unchanged."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    app: FastAPI
    _resolver: DirectoryRecipientResolver
    app, _resolver = _bulk_app(db, audit, events, activity)
    primary: IssueRow = _issue("read-only-primary", 72)
    child: IssueRow = _issue("read-only-child", 73)
    primary.needs_review = True
    child.needs_review = True
    clock: FakeClock = FakeClock(NOW)
    token: CreatedToken
    async with db.sessions.begin() as session:
        session.add_all((primary, child))
        token = await TokenService(audit, clock).create(
            session, "bulk-read-only", frozenset((ApiScope.READ,)), None
        )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses: tuple[Response, ...] = (
            await client.post(
                "/api/issues/bulk",
                headers={"Authorization": f"Bearer {token.token}"},
                json={"ids": [primary.id], "action": "mark_reviewed"},
            ),
            await client.post(
                "/api/issues/merge",
                headers={"Authorization": f"Bearer {token.token}"},
                json={"primary_id": primary.id, "ids": [child.id]},
            ),
            await client.post(
                f"/api/issues/{primary.id}/unmerge",
                headers={"Authorization": f"Bearer {token.token}"},
                json={"ids": [child.id]},
            ),
        )
    assert tuple(response.status_code for response in responses) == (403, 403, 403)
    assert not any(entry.action.startswith("issue.bulk") for entry in audit.entries)
    async with db.sessions() as session:
        stored_primary: IssueRow | None = await session.get(IssueRow, primary.id)
        stored_child: IssueRow | None = await session.get(IssueRow, child.id)
    assert stored_primary is not None and stored_primary.needs_review is True
    assert stored_primary.merged_into_id is None
    assert stored_child is not None and stored_child.needs_review is True
    assert stored_child.merged_into_id is None
    await db.close()
