"""Issue action and triage route status/permission regressions."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select

from telescreen.api.common import ApiContext, install_error_handlers
from telescreen.api.routers.issue_triage import IssueTriageRouterDeps, build_issue_triage_router
from telescreen.api.routers.issues_actions import build_issues_actions_router
from telescreen.api.routers.issues_common import IssuesRouterDeps
from telescreen.core.clock import FakeClock
from telescreen.core.vocabulary import ApiScope, IssueHistoryKind, IssuePriority, IssueStatus
from telescreen.db.models import IssueHistoryRow, IssueRow
from telescreen.directory import CreatedToken, DirectoryRecipientResolver, TokenService
from telescreen.issues.service import IssueService
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from tests.issues.v13_lifecycle.test_archive import (
    NOW,
    RecordingActivity,
    RecordingAudit,
    RecordingEvents,
    RecordingIndexer,
    _issue,
    _world,
)
from tests.support.db import SqliteHarness


@pytest.mark.parametrize(
    "status",
    (
        IssueStatus.ARCHIVED,
        IssueStatus.ESCALATING,
        IssueStatus.RESOLVED,
        IssueStatus.IGNORED,
    ),
)
@pytest.mark.asyncio
async def test_patch_rejects_dedicated_status_transitions_without_mutating_row(
    status: IssueStatus,
) -> None:
    """PATCH rejects dedicated lifecycle statuses and preserves the DB row."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    issue: IssueRow = _issue(f"patch-status-{status.value}", 40)
    service: IssueService = IssueService(
        audit,
        events,
        Fingerprinter(b"r" * 32, "test-key"),
        Redactor.default(),
        DirectoryRecipientResolver(),
        FakeClock(NOW),
        activity=activity,
    )
    ctx: ApiContext = ApiContext(db.sessions, FakeClock(NOW), audit)
    app: FastAPI = FastAPI()
    install_error_handlers(app)
    app.include_router(
        build_issues_actions_router(IssuesRouterDeps(ctx, service, RecordingIndexer([])))
    )
    async with db.sessions.begin() as session:
        session.add(issue)
        token: CreatedToken = await TokenService(audit, FakeClock(NOW)).create(
            session, "write-triage", frozenset((ApiScope.WRITE,)), None
        )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response: Response = await client.patch(
            f"/api/issues/{issue.id}",
            headers={"Authorization": f"Bearer {token.token}"},
            json={"status": status.value},
        )
    async with db.sessions() as session:
        stored: IssueRow | None = await session.get(IssueRow, issue.id)
    assert response.status_code == 422
    assert b'"code":"invalid_transition"' in response.content
    assert stored is not None and stored.status == IssueStatus.UNRESOLVED.value
    await db.close()


@pytest.mark.asyncio
async def test_patch_priority_persists_manual_flag_and_one_history_row() -> None:
    """PATCH priority writes the manual triage field and one history record."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    issue: IssueRow = _issue("patch-priority", 41)
    service: IssueService = IssueService(
        audit,
        events,
        Fingerprinter(b"s" * 32, "test-key"),
        Redactor.default(),
        DirectoryRecipientResolver(),
        FakeClock(NOW),
        activity=activity,
    )
    ctx: ApiContext = ApiContext(db.sessions, FakeClock(NOW), audit)
    app: FastAPI = FastAPI()
    install_error_handlers(app)
    app.include_router(
        build_issues_actions_router(IssuesRouterDeps(ctx, service, RecordingIndexer([])))
    )
    async with db.sessions.begin() as session:
        session.add(issue)
        token: CreatedToken = await TokenService(audit, FakeClock(NOW)).create(
            session, "write-priority", frozenset((ApiScope.WRITE,)), None
        )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response: Response = await client.patch(
            f"/api/issues/{issue.id}",
            headers={"Authorization": f"Bearer {token.token}"},
            json={"priority": IssuePriority.LOW.value},
        )
    async with db.sessions() as session:
        stored: IssueRow | None = await session.get(IssueRow, issue.id)
        history: tuple[IssueHistoryRow, ...] = tuple(
            (
                await session.scalars(
                    select(IssueHistoryRow).where(IssueHistoryRow.issue_id == issue.id)
                )
            ).all()
        )
    assert response.status_code == 200
    assert stored is not None and stored.priority == IssuePriority.LOW.value
    assert stored.priority_manual is True
    assert sum(row.kind == IssueHistoryKind.PRIORITY_CHANGED.value for row in history) == 1
    await db.close()


@pytest.mark.asyncio
async def test_archive_route_requires_issues_write_permission() -> None:
    """A real read-only API token cannot archive and leaves the row unchanged."""
    db: SqliteHarness
    audit: RecordingAudit
    events: RecordingEvents
    activity: RecordingActivity
    db, audit, events, activity = await _world()
    issue: IssueRow = _issue("archive-forbidden", 42)
    clock: FakeClock = FakeClock(NOW)
    service: IssueService = IssueService(
        audit,
        events,
        Fingerprinter(b"u" * 32, "test-key"),
        Redactor.default(),
        DirectoryRecipientResolver(),
        clock,
        activity=activity,
    )
    ctx: ApiContext = ApiContext(db.sessions, clock, audit)
    indexer: RecordingIndexer = RecordingIndexer([])
    app: FastAPI = FastAPI()
    install_error_handlers(app)
    app.include_router(build_issue_triage_router(IssueTriageRouterDeps(ctx, service, indexer)))
    async with db.sessions.begin() as session:
        session.add(issue)
        token: CreatedToken = await TokenService(audit, clock).create(
            session, "read-only-triage", frozenset((ApiScope.READ,)), None
        )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response: Response = await client.post(
            f"/api/issues/{issue.id}/archive",
            headers={"Authorization": f"Bearer {token.token}"},
            json={"kind": "until_findings", "count": 5},
        )
    async with db.sessions() as session:
        stored: IssueRow | None = await session.get(IssueRow, issue.id)
    assert response.status_code == 403
    assert stored is not None and stored.status == IssueStatus.UNRESOLVED.value
    assert stored.archive_kind is None and stored.archive_until is None
    await db.close()
