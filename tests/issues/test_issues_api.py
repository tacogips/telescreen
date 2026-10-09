"""Resume coverage for issue APIs and failed push scan completion."""

from dataclasses import dataclass
from datetime import datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Headers
from httpx import Response as HttpxResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.common import ApiContext, install_error_handlers
from telescreen.api.routers.issues import IssuesRouterDeps, build_issues_router
from telescreen.core.json import JsonKind, JsonValue, json_object, parse_json
from telescreen.core.vocabulary import (
    ActorKind,
    ApiScope,
    CollectorKind,
    DetectorKind,
    ErrorCode,
    IssueHistoryKind,
    IssueStatus,
    RuleClass,
    ScanJobStatus,
    ScanTrigger,
    TriggerMode,
)
from telescreen.db.models import IssueHistoryRow, IssueRow, MemberRow
from telescreen.directory import CreatedToken, TokenService
from telescreen.issues.ingest import IngestService
from telescreen.ports.audit import AuditEntry
from telescreen.ports.collectors import CollectorConfig, CustomSelector, ResourceContent
from telescreen.ports.findings import MaskedFinding, RecordOutcome
from tests.issues.test_issues import (
    FakeCollectorConfigReader,
    IssueHarness,
    RecordingIndexer,
    make_issue_harness,
    masked_finding,
    resource_scan,
)


@dataclass
class RecordingScanJobs:
    """Capture count-only scan job completion calls."""

    completions: list[tuple[str, ScanJobStatus, int, int, int, ErrorCode | None]]

    async def create(
        self,
        session: AsyncSession,
        collector_config_id: str | None,
        source: str,
        trigger: ScanTrigger,
        started_at: datetime,
    ) -> str:
        """Return one opaque id without retaining request content."""
        del session, collector_config_id, source, trigger, started_at
        return "scan-failed-test"

    async def finish(
        self,
        session: AsyncSession,
        scan_job_id: str,
        status: ScanJobStatus,
        resources_scanned: int,
        resources_skipped: int,
        findings_count: int,
        error_code: ErrorCode | None,
        finished_at: datetime,
    ) -> None:
        """Record safe completion metadata for the assertion."""
        del session, finished_at
        self.completions.append(
            (scan_job_id, status, resources_scanned, resources_skipped, findings_count, error_code)
        )


@dataclass
class FailingScanner:
    """Raise on the second resource to exercise scan job failure handling."""

    calls: int = 0

    async def scan_resource(
        self,
        content: ResourceContent,
        scan_job_id: str,
        detectors: tuple[DetectorKind, ...] | None = None,
    ) -> RecordOutcome:
        """Fail deterministically after one completed resource."""
        del content, scan_job_id, detectors
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("scanner failure")
        return RecordOutcome.empty()


@pytest.mark.asyncio
async def test_scan_push_finishes_failed_job_before_reraising() -> None:
    """Persist a failed scan job when a resource scanner raises mid-loop."""
    harness: IssueHarness = await make_issue_harness()
    try:
        config: CollectorConfig = CollectorConfig(
            "collector-1",
            "demo",
            CollectorKind.CUSTOM,
            TriggerMode.BOTH,
            None,
            None,
            1,
            CustomSelector(json_object(())),
            None,
            True,
        )
        scan_jobs: RecordingScanJobs = RecordingScanJobs([])
        ingest: IngestService = IngestService(
            harness.db.sessions,
            FailingScanner(),
            harness.service,
            FakeCollectorConfigReader(config),
            scan_jobs,
            RecordingIndexer([], []),
            harness.clock,
        )
        resources: tuple[ResourceContent, ...] = tuple(
            ResourceContent(
                "custom:demo",
                None,
                (),
                True,
                False,
                f"resource:{index}",
                f"Resource {index}",
            )
            for index in range(2)
        )
        with pytest.raises(RuntimeError, match="scanner failure"):
            await ingest.scan_push("custom:demo", resources)
        assert scan_jobs.completions == [
            (
                "scan-failed-test",
                ScanJobStatus.FAILED,
                1,
                0,
                0,
                ErrorCode.INTERNAL_ERROR,
            )
        ]
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_issue_api_resume_filters_paging_transitions_and_actor() -> None:
    """Cover p16 API filters, keyset pages, transitions, and user audit identity."""
    harness: IssueHarness = await make_issue_harness()
    try:
        token_one: MaskedFinding = masked_finding(harness, RuleClass.TOKEN, "token-one")
        token_two: MaskedFinding = masked_finding(harness, RuleClass.TOKEN, "token-two")
        email: MaskedFinding = masked_finding(harness, RuleClass.EMAIL, "email-one")
        phone: MaskedFinding = masked_finding(harness, RuleClass.PHONE, "phone-one")
        credential: MaskedFinding = masked_finding(harness, RuleClass.CREDENTIAL, "credential-one")
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session,
                resource_scan(harness, (token_one, email, phone, credential), "api-scan-1"),
            )
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session, resource_scan(harness, (token_two,), "api-scan-2")
            )
            session.add(
                MemberRow(
                    id="member-owner",
                    handle="owner",
                    display_name="Owner",
                    email=None,
                    slack_user_id=None,
                    active=True,
                    created_at=harness.clock.now(),
                    updated_at=harness.clock.now(),
                )
            )
        async with harness.db.sessions.begin() as session:
            issue: IssueRow | None = await session.scalar(
                select(IssueRow).where(IssueRow.rule_class == RuleClass.TOKEN.value)
            )
            assert issue is not None
            issue.assignee_member_id = "member-owner"
            token_service: TokenService = TokenService(harness.audit, harness.clock)
            api_token: CreatedToken = await token_service.create(
                session, "issues-resume", frozenset((ApiScope.READ, ApiScope.WRITE)), None
            )
        ctx: ApiContext = ApiContext(harness.db.sessions, harness.clock, harness.audit)
        app: FastAPI = FastAPI()
        install_error_handlers(app)
        app.include_router(
            build_issues_router(IssuesRouterDeps(ctx, harness.service, RecordingIndexer([], [])))
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers: Headers = Headers({"Authorization": f"Bearer {api_token.token}"})

            async def list_count(path: str) -> int:
                """Return the number of rows in one issue list response."""
                response: HttpxResponse = await client.get(path, headers=headers)
                assert response.status_code == 200
                payload: JsonValue = parse_json(response.content)
                items: JsonValue | None = payload.get("items")
                assert items is not None and items.kind is JsonKind.ARRAY
                return len(items.items)

            assert await list_count("/api/issues?source=custom%3Atest") == 4
            assert await list_count("/api/issues?source=custom%3Aother") == 0
            assert await list_count("/api/issues?rule_class=token") == 1
            assert await list_count("/api/issues?severity=high") == 2
            assert await list_count("/api/issues?assignee=member%3Aowner") == 1
            assert await list_count("/api/issues?q=Test%20resource") == 4
            assert await list_count("/api/issues?q=no-match") == 0

            first_page: HttpxResponse = await client.get(
                "/api/issues?sort=times_seen&limit=2", headers=headers
            )
            first_payload: JsonValue = parse_json(first_page.content)
            first_items: JsonValue | None = first_payload.get("items")
            cursor_value: JsonValue | None = first_payload.get("next_cursor")
            assert first_items is not None and first_items.kind is JsonKind.ARRAY
            assert len(first_items.items) == 2
            first_count: JsonValue | None = first_items.items[0].get("times_seen")
            second_count: JsonValue | None = first_items.items[1].get("times_seen")
            assert first_count is not None and first_count.as_int() == 2
            assert second_count is not None and second_count.as_int() == 1
            assert cursor_value is not None and cursor_value.as_str() is not None
            cursor: str = cursor_value.as_str() or ""
            second_page: HttpxResponse = await client.get(
                f"/api/issues?sort=times_seen&limit=2&cursor={cursor}", headers=headers
            )
            second_payload: JsonValue = parse_json(second_page.content)
            second_items: JsonValue | None = second_payload.get("items")
            assert second_page.status_code == 200
            assert second_items is not None and second_items.kind is JsonKind.ARRAY
            assert len(second_items.items) == 2
            first_ids: set[str] = _issue_ids(first_items)
            second_ids: set[str] = _issue_ids(second_items)
            assert first_ids.isdisjoint(second_ids)

            token_issue_row: IssueRow | None
            async with harness.db.sessions() as session:
                token_issue_row = await session.scalar(
                    select(IssueRow).where(IssueRow.rule_class == RuleClass.TOKEN.value)
                )
            assert token_issue_row is not None
            event_first: HttpxResponse = await client.get(
                f"/api/issues/{token_issue_row.id}/events?limit=1", headers=headers
            )
            event_payload: JsonValue = parse_json(event_first.content)
            event_cursor_value: JsonValue | None = event_payload.get("next_cursor")
            event_items: JsonValue | None = event_payload.get("items")
            assert event_first.status_code == 200
            assert event_items is not None and len(event_items.items) == 1
            assert event_cursor_value is not None and event_cursor_value.as_str() is not None
            event_second: HttpxResponse = await client.get(
                f"/api/issues/{token_issue_row.id}/events?limit=1&cursor={event_cursor_value.as_str()}",
                headers=headers,
            )
            event_second_payload: JsonValue = parse_json(event_second.content)
            event_second_items: JsonValue | None = event_second_payload.get("items")
            assert event_second.status_code == 200
            assert event_second_items is not None and len(event_second_items.items) == 1

            stats: HttpxResponse = await client.get(
                "/api/issues/stats?bucket=day&days=3", headers=headers
            )
            stats_payload: JsonValue = parse_json(stats.content)
            buckets: JsonValue | None = stats_payload.get("buckets")
            assert stats.status_code == 200
            assert buckets is not None and len(buckets.items) == 3
            counts: list[int] = []
            for bucket in buckets.items:
                count_value: JsonValue | None = bucket.get("count")
                assert count_value is not None and count_value.as_int() is not None
                counts.append(count_value.as_int() or 0)
            assert counts[0:2] == [0, 0]
            assert counts[2] == 5

            changed: HttpxResponse = await client.patch(
                f"/api/issues/{token_issue_row.id}",
                headers=headers,
                json={"status": IssueStatus.ACKNOWLEDGED.value},
            )
            assert changed.status_code == 200
            assert b'"status":"acknowledged"' in changed.content
            audit_entry: AuditEntry = next(
                entry
                for entry in reversed(harness.audit.entries)
                if entry.action == "issue.status_changed"
            )
            assert audit_entry.actor_ref == f"token:{api_token.token_id}"
            assert audit_entry.actor_kind is ActorKind.TOKEN
            invalid: HttpxResponse = await client.patch(
                f"/api/issues/{token_issue_row.id}",
                headers=headers,
                json={"status": IssueStatus.REGRESSED.value},
            )
            assert invalid.status_code == 409
            assert b"invalid_transition" in invalid.content

        async with harness.db.sessions() as session:
            history: list[IssueHistoryRow] = list(
                (await session.scalars(select(IssueHistoryRow))).all()
            )
        assert any(
            entry.kind == IssueHistoryKind.STATUS_CHANGED.value
            and entry.actor_ref == f"token:{api_token.token_id}"
            for entry in history
        )
    finally:
        await harness.close()


def _issue_ids(items: JsonValue) -> set[str]:
    """Extract issue identifiers from a JSON page."""
    identifiers: set[str] = set()
    for item in items.items:
        identifier: JsonValue | None = item.get("id")
        if identifier is not None and identifier.as_str() is not None:
            identifiers.add(identifier.as_str() or "")
    return identifiers
