"""End-to-end checks for v1.3 composition and route registration."""

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient, MockTransport
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.alerts.maintenance import AlertMaintenanceProvider
from telescreen.api.app import create_app
from telescreen.core.config import TelescreenSettings
from telescreen.core.secrets import EnvSource
from telescreen.core.vocabulary import (
    ApiScope,
    EngineTaskKind,
    IssueEventKind,
    RuleClass,
    Severity,
)
from telescreen.directory.tokens import CreatedToken
from telescreen.issues.activity.notifier import SubscriptionActivityNotifier
from telescreen.issues.archive import ArchiveMaintenanceProvider
from telescreen.issues.weekly_report import WeeklyReportHandler, WeeklyReportProvider
from telescreen.ports.issues import IssueEvent, IssueEventSink
from telescreen.ports.workflow import TaskHandler
from telescreen.wiring import (
    Container,
    DeferredIssueEventSink,
    build_container,
    close_container,
)
from telescreen.wiring_v13 import IssueEventFanout
from telescreen.workflow.triggers.service import WorkflowTriggerService
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass(frozen=True)
class _TestEnv(EnvSource):
    """Supply only the test fingerprint key."""

    def get(self, key: str, /) -> str | None:
        """Resolve the local test key by name."""
        return "v" * 32 if key == "TELESCREEN_TEST_KEY" else None


@dataclass
class _SpySink(IssueEventSink):
    """Record one event delivery and optionally fail."""

    name: str
    calls: list[str]
    fail: bool = False

    async def on_issue_event(self, session: AsyncSession, event: IssueEvent) -> None:
        """Record invocation and optionally propagate a failure."""
        del session, event
        self.calls.append(self.name)
        if self.fail:
            raise RuntimeError("sink failed")


def _event() -> IssueEvent:
    """Build a safe lifecycle event for fan-out tests."""
    return IssueEvent(
        IssueEventKind.ISSUE_CREATED,
        "event-1",
        "issue-1",
        "test",
        RuleClass.CREDENTIAL,
        Severity.HIGH,
        datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_v13_container_wiring_and_static_issue_routes() -> None:
    """Register v1.3 ports and routes before dynamic issue id routes."""
    database: SqliteHarness = await make_sqlite_harness()
    outbound: httpx.AsyncClient = httpx.AsyncClient(
        transport=MockTransport(lambda request: httpx.Response(200))
    )
    settings: TelescreenSettings = TelescreenSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        fingerprint_key_ref="env:TELESCREEN_TEST_KEY",
    )
    container: Container = build_container(settings, _TestEnv(), outbound, database.sessions)
    try:
        issues_router: APIRouter = container.v13.routers[-1]
        assert container.routers[: len(container.v13.routers)] == container.v13.routers
        assert all(
            container.routers.index(router) < container.routers.index(issues_router)
            for router in container.v13.routers[:-1]
        )
        assert container.issue_service.findings_observer is container.v13.alert_evaluator
        assert container.issue_service.activity is container.v13.activity
        assert isinstance(container.v13.activity, SubscriptionActivityNotifier)
        assert container.v13.activity.actors is container.v13.actors
        assert container.v13.issues_router_deps.actors is container.v13.actors
        assert container.v13.issues_router_deps.suggester is container.v13.suggester
        assert container.v13.issues_router_deps.member_notifier is not None
        assert isinstance(container.issue_service.events, DeferredIssueEventSink)
        deferred: DeferredIssueEventSink = container.issue_service.events
        assert isinstance(deferred.target, IssueEventFanout)
        assert isinstance(deferred.target.sinks[0], WorkflowTriggerService)
        assert deferred.target.sinks[1] is container.v13.alert_evaluator
        assert any(
            isinstance(item, ArchiveMaintenanceProvider)
            for item in container.worker.scheduler.providers
        )
        assert any(
            isinstance(item, AlertMaintenanceProvider)
            for item in container.worker.scheduler.providers
        )
        assert any(
            isinstance(item, WeeklyReportProvider) for item in container.worker.scheduler.providers
        )
        weekly: TaskHandler | None = container.worker.registry.get(EngineTaskKind.WEEKLY_REPORT)
        assert isinstance(weekly, WeeklyReportHandler)

        async with database.sessions.begin() as session:
            token: CreatedToken = await container.token_service.create(
                session, "v13-wiring", frozenset((ApiScope.ADMIN,)), None
            )
        app: FastAPI = create_app(container)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            counts: httpx.Response = await client.get(
                "/api/issues/counts", headers={"Authorization": f"Bearer {token.token}"}
            )
            assert counts.status_code == 200, counts.text
            assert "tabs" in counts.json()
            bulk: httpx.Response = await client.post(
                "/api/issues/bulk",
                headers={"Authorization": f"Bearer {token.token}"},
                json={"action": "mark_reviewed", "ids": []},
            )
            assert bulk.status_code not in (404, 405), bulk.text
    finally:
        await close_container(container)
        await database.close()
        await outbound.aclose()


@pytest.mark.asyncio
async def test_issue_event_fanout_is_ordered_and_propagates_exceptions() -> None:
    """Call sinks in order with one session and stop when a sink raises."""
    database: SqliteHarness = await make_sqlite_harness()
    calls: list[str] = []
    first: _SpySink = _SpySink("workflow", calls)
    second: _SpySink = _SpySink("alerts", calls)
    fanout: IssueEventFanout = IssueEventFanout((first, second))
    try:
        async with database.sessions() as session:
            await fanout.on_issue_event(session, _event())
        assert calls == ["workflow", "alerts"]
        calls.clear()
        failing: _SpySink = _SpySink("failure", calls, True)
        with pytest.raises(RuntimeError, match="sink failed"):
            async with database.sessions() as session:
                await IssueEventFanout((failing, second)).on_issue_event(session, _event())
        assert calls == ["failure"]
    finally:
        await database.close()
