"""Issue lifecycle and scan-pipeline behavioral tests."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Headers
from httpx import Response as HttpxResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.common import ApiContext, install_error_handlers
from telescreen.api.routers.ingest import IngestRouterDeps, build_ingest_router
from telescreen.api.routers.issues import IssuesRouterDeps, build_issues_router
from telescreen.core.clock import FakeClock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonKind, JsonValue, json_object, json_str, parse_json
from telescreen.core.vocabulary import (
    ApiScope,
    CollectorKind,
    DetectorKind,
    ErrorCode,
    IssueEventKind,
    IssueHistoryKind,
    IssueStatus,
    RuleClass,
    ScanJobStatus,
    ScanTrigger,
    Severity,
    TriggerMode,
)
from telescreen.db.models import FindingRow, IssueHistoryRow, IssueRow
from telescreen.detection import Detector, RuleDetector
from telescreen.directory import CreatedToken, DirectoryRecipientResolver, TokenService
from telescreen.issues.ingest import IngestService
from telescreen.issues.scan import ResourceScannerImpl
from telescreen.issues.service import IssueService
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.masking.seal import Masker
from telescreen.ports.audit import AuditEntry
from telescreen.ports.collectors import (
    CollectorConfig,
    CustomSelector,
    ResourceContent,
    Segment,
)
from telescreen.ports.findings import (
    ExternalFinding,
    ExternalScan,
    MaskedFinding,
    RecordOutcome,
    ResourceScan,
)
from telescreen.ports.issues import IssueEvent
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import Sample, assert_no_leaks, github_token


@dataclass
class RecordingAudit:
    """Capture safe audit entries for service tests."""

    entries: list[AuditEntry]

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Record the entry without creating a second audit row."""
        del session
        self.entries.append(entry)
        return len(self.entries)


@dataclass
class RecordingEvents:
    """Capture transaction-scoped issue events."""

    events: list[IssueEvent]

    async def on_issue_event(self, session: AsyncSession, event: IssueEvent) -> None:
        """Record the event for assertions."""
        del session
        self.events.append(event)


@dataclass
class RecordingIndexer:
    """Capture post-commit indexing calls."""

    issue_ids: list[str]
    finding_ids: list[str]

    async def index_issue(self, issue_id: str) -> None:
        """Record indexed issue ids."""
        self.issue_ids.append(issue_id)

    async def index_finding(self, finding_id: str) -> None:
        """Record indexed finding ids."""
        self.finding_ids.append(finding_id)

    async def index_run(self, run_id: str) -> None:
        """Satisfy the shared search protocol."""
        del run_id


@dataclass
class FakeCollectorConfigReader:
    """Return one optional custom collector configuration."""

    config: CollectorConfig | None

    async def get(self, session: AsyncSession, collector_config_id: str) -> CollectorConfig | None:
        """Return the configured collector when ids match."""
        del session
        return (
            self.config
            if self.config is not None and self.config.id == collector_config_id
            else None
        )

    async def get_by_name(self, session: AsyncSession, name: str) -> CollectorConfig | None:
        """Return the configured collector when names match."""
        del session
        return self.config if self.config is not None and self.config.name == name else None


class FakeScanJobStore:
    """No-op scan-job port for push route tests."""

    async def create(
        self,
        session: AsyncSession,
        collector_config_id: str | None,
        source: str,
        trigger: ScanTrigger,
        started_at: datetime,
    ) -> str:
        """Return a fixed opaque id without retaining request data."""
        del session, collector_config_id, source, trigger, started_at
        return "scan-test"

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
        """Accept completion metadata for protocol conformance."""
        del (
            session,
            scan_job_id,
            status,
            resources_scanned,
            resources_skipped,
            findings_count,
            error_code,
            finished_at,
        )


@dataclass
class IssueHarness:
    """Own all service dependencies for one isolated test."""

    db: SqliteHarness
    clock: FakeClock
    service: IssueService
    events: RecordingEvents
    audit: RecordingAudit
    fingerprinter: Fingerprinter
    redactor: Redactor

    async def close(self) -> None:
        """Dispose of the isolated database."""
        await self.db.close()


async def make_issue_harness() -> IssueHarness:
    """Create an issue service over the standard SQLite harness."""
    db: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 8, tzinfo=UTC))
    events: RecordingEvents = RecordingEvents([])
    audit: RecordingAudit = RecordingAudit([])
    fingerprinter: Fingerprinter = Fingerprinter(b"x" * 32, "test-key")
    redactor: Redactor = Redactor.default()
    service: IssueService = IssueService(
        audit, events, fingerprinter, redactor, DirectoryRecipientResolver(), clock
    )
    return IssueHarness(db, clock, service, events, audit, fingerprinter, redactor)


def masked_finding(harness: IssueHarness, rule_class: RuleClass, marker: str) -> MaskedFinding:
    """Build safe test metadata with a deterministic keyed fingerprint."""
    return MaskedFinding(
        f"test.{rule_class.value}",
        rule_class,
        Severity.HIGH,
        DetectorKind.RULE,
        0.99,
        "value [REDACTED]",
        "segment:1",
        harness.fingerprinter.fingerprint(f"test.{rule_class.value}", marker, rule_class),
        harness.fingerprinter.key_id,
    )


def resource_scan(
    harness: IssueHarness,
    findings: tuple[MaskedFinding, ...],
    scan_job_id: str | None,
    complete: bool = True,
    sweep_covered: bool = False,
    resource_locator: str = "resource:1",
) -> ResourceScan:
    """Build a resource scan DTO for a stable test resource."""
    return ResourceScan(
        "custom:test",
        harness.fingerprinter.resource_key("custom:test", resource_locator),
        resource_locator,
        "Test resource",
        "collector-1",
        scan_job_id,
        None,
        findings,
        complete,
        sweep_covered,
        harness.clock.now(),
    )


@pytest.mark.asyncio
async def test_grouping_counts_duplicate_scans_auto_resolve_and_regress() -> None:
    """Group by rule class and preserve lifecycle counts across rescans."""
    harness: IssueHarness = await make_issue_harness()
    try:
        email_one: MaskedFinding = masked_finding(harness, RuleClass.EMAIL, "email-one")
        email_two: MaskedFinding = masked_finding(harness, RuleClass.EMAIL, "email-two")
        token: MaskedFinding = masked_finding(harness, RuleClass.TOKEN, "token-one")
        async with harness.db.sessions.begin() as session:
            first: RecordOutcome = await harness.service.record_scan(
                session, resource_scan(harness, (email_one, email_two, token), "scan-1")
            )
        assert first.issues_opened == 2
        assert first.findings_inserted == 3
        assert len(harness.events.events) == 2
        assert all(event.kind is IssueEventKind.ISSUE_CREATED for event in harness.events.events)

        async with harness.db.sessions.begin() as session:
            duplicate: RecordOutcome = await harness.service.record_scan(
                session, resource_scan(harness, (email_one, email_two, token), "scan-1")
            )
        assert duplicate.findings_inserted == 0
        assert len(duplicate.issue_ids) == 2

        harness.clock.advance(timedelta(hours=1))
        async with harness.db.sessions.begin() as session:
            second: RecordOutcome = await harness.service.record_scan(
                session, resource_scan(harness, (email_one, email_two, token), "scan-2")
            )
        assert second.findings_inserted == 3
        async with harness.db.sessions() as session:
            issues: list[IssueRow] = list((await session.scalars(select(IssueRow))).all())
        email_issue: IssueRow = next(
            issue for issue in issues if issue.rule_class == RuleClass.EMAIL.value
        )
        token_issue: IssueRow = next(
            issue for issue in issues if issue.rule_class == RuleClass.TOKEN.value
        )
        assert email_issue.times_seen == 4
        assert email_issue.distinct_fingerprints == 2
        assert ensure_utc(email_issue.last_seen) == harness.clock.now()

        harness.clock.advance(timedelta(hours=1))
        async with harness.db.sessions.begin() as session:
            clean: RecordOutcome = await harness.service.record_scan(
                session, resource_scan(harness, (), "scan-3")
            )
        assert clean.issues_auto_resolved == 2
        async with harness.db.sessions() as session:
            resolved_token: IssueRow | None = await session.get(IssueRow, token_issue.id)
            history: list[IssueHistoryRow] = list(
                (await session.scalars(select(IssueHistoryRow))).all()
            )
        assert resolved_token is not None
        assert resolved_token.status == IssueStatus.RESOLVED.value
        assert resolved_token.resolution == "auto"
        assert any(item.kind == IssueHistoryKind.AUTO_RESOLVED.value for item in history)

        harness.clock.advance(timedelta(hours=1))
        async with harness.db.sessions.begin() as session:
            regressed: RecordOutcome = await harness.service.record_scan(
                session,
                resource_scan(
                    harness,
                    (masked_finding(harness, RuleClass.TOKEN, "token-again"),),
                    "scan-4",
                ),
            )
        assert regressed.issues_regressed == 1
        assert any(event.kind is IssueEventKind.ISSUE_REGRESSED for event in harness.events.events)
        async with harness.db.sessions() as session:
            current_token: IssueRow | None = await session.get(IssueRow, token_issue.id)
        assert current_token is not None
        assert current_token.status == IssueStatus.REGRESSED.value
        assert current_token.regression_count == 1
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ignored_issue_only_updates_counters() -> None:
    """A new observation on an ignored issue does not regress or emit events."""
    harness: IssueHarness = await make_issue_harness()
    try:
        finding: MaskedFinding = masked_finding(harness, RuleClass.EMAIL, "email-one")
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(session, resource_scan(harness, (finding,), "scan-1"))
        event_count: int = len(harness.events.events)
        async with harness.db.sessions.begin() as session:
            issue: IssueRow = list((await session.scalars(select(IssueRow))).all())[0]
            await harness.service.change_status(
                session, issue.id, IssueStatus.IGNORED, "member:test"
            )
        async with harness.db.sessions.begin() as session:
            outcome: RecordOutcome = await harness.service.record_scan(
                session,
                resource_scan(
                    harness,
                    (masked_finding(harness, RuleClass.EMAIL, "email-two"),),
                    "scan-2",
                ),
            )
        assert outcome.issues_regressed == 0
        assert len(harness.events.events) == event_count
        async with harness.db.sessions() as session:
            issue = list((await session.scalars(select(IssueRow))).all())[0]
        assert issue.status == IssueStatus.IGNORED.value
        assert issue.times_seen == 2
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_resolve_unchecked_only_resolves_old_sweep_covered_issues() -> None:
    """Removal reconciliation respects collector, sweep coverage, and watermark."""
    harness: IssueHarness = await make_issue_harness()
    try:
        covered_finding: MaskedFinding = masked_finding(harness, RuleClass.EMAIL, "email-one")
        uncovered_finding: MaskedFinding = masked_finding(harness, RuleClass.TOKEN, "token-one")
        recent_finding: MaskedFinding = masked_finding(
            harness, RuleClass.PRIVATE_KEY, "private-key-one"
        )
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session,
                resource_scan(
                    harness,
                    (covered_finding,),
                    "scan-1",
                    sweep_covered=True,
                    resource_locator="covered-old",
                ),
            )
            await harness.service.record_scan(
                session,
                resource_scan(
                    harness,
                    (uncovered_finding,),
                    "scan-2",
                    sweep_covered=False,
                    resource_locator="uncovered-old",
                ),
            )
        harness.clock.advance(timedelta(hours=1))
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session,
                resource_scan(
                    harness,
                    (recent_finding,),
                    "scan-3",
                    sweep_covered=True,
                    resource_locator="covered-recent",
                ),
            )
        async with harness.db.sessions.begin() as session:
            count: int = await harness.service.resolve_unchecked(
                session, "collector-1", harness.clock.now() - timedelta(minutes=1)
            )
        assert count == 1
        async with harness.db.sessions() as session:
            issues: list[IssueRow] = list((await session.scalars(select(IssueRow))).all())
        covered_status: str = next(
            issue.status for issue in issues if issue.display_locator == "covered-old"
        )
        uncovered_status: str = next(
            issue.status for issue in issues if issue.display_locator == "uncovered-old"
        )
        recent_status: str = next(
            issue.status for issue in issues if issue.display_locator == "covered-recent"
        )
        assert covered_status == IssueStatus.RESOLVED.value
        assert uncovered_status == IssueStatus.UNRESOLVED.value
        assert recent_status == IssueStatus.UNRESOLVED.value
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_resource_scanner_skips_oversize_without_resolving_issue() -> None:
    """An oversize resource produces an incomplete skipped scan."""
    harness: IssueHarness = await make_issue_harness()
    try:
        finding: MaskedFinding = masked_finding(harness, RuleClass.EMAIL, "email-one")
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(session, resource_scan(harness, (finding,), "scan-1"))
        indexer: RecordingIndexer = RecordingIndexer([], [])
        detector: RuleDetector = RuleDetector()
        masker: Masker = Masker(harness.fingerprinter, harness.redactor)

        def factory(kinds: tuple[DetectorKind, ...]) -> Detector:
            """Return the fixed rule detector for this scan test."""
            del kinds
            return detector

        scanner: ResourceScannerImpl = ResourceScannerImpl(
            harness.db.sessions,
            detector,
            factory,
            masker,
            harness.fingerprinter,
            harness.redactor,
            harness.service,
            indexer,
            harness.clock,
            4,
            8,
        )
        content: ResourceContent = ResourceContent(
            "custom:test",
            "collector-1",
            (Segment("segment:1", "ordinary text"),),
            True,
            False,
            "resource:1",
            "Test resource",
        )
        outcome: RecordOutcome = await scanner.scan_resource(content, "scan-2")
        assert outcome.skipped
        async with harness.db.sessions() as session:
            issue: IssueRow = list((await session.scalars(select(IssueRow))).all())[0]
        assert issue.status == IssueStatus.UNRESOLVED.value
        assert ensure_utc(issue.last_checked_at) == harness.clock.now()
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_external_findings_are_checked_and_rekeyed() -> None:
    """Reject raw external fields and never persist an external fingerprint."""
    harness: IssueHarness = await make_issue_harness()
    sample: Sample = github_token()
    try:
        raw: ExternalFinding = ExternalFinding(
            "repo/file", "token.github", RuleClass.TOKEN, sample.value, "line 1", 0.9, "external-fp"
        )
        async with harness.db.sessions.begin() as session:
            with pytest.raises(TelescreenError):
                await harness.service.record_external(
                    session,
                    ExternalScan(
                        "custom:test",
                        "collector-1",
                        "scan-1",
                        DetectorKind.POLICE,
                        (raw,),
                        (),
                        False,
                        harness.clock.now(),
                    ),
                )
        async with harness.db.sessions.begin() as session:
            with pytest.raises(TelescreenError):
                await harness.service.record_external(
                    session,
                    ExternalScan(
                        "custom:test",
                        "collector-1",
                        "scan-1",
                        DetectorKind.POLICE,
                        (),
                        (sample.value,),
                        False,
                        harness.clock.now(),
                    ),
                )
        clean: ExternalFinding = ExternalFinding(
            "repo/file",
            "token.github",
            RuleClass.TOKEN,
            "[REDACTED]",
            "line 1",
            0.9,
            "external-fp",
        )
        async with harness.db.sessions.begin() as session:
            outcome: RecordOutcome = await harness.service.record_external(
                session,
                ExternalScan(
                    "custom:test",
                    "collector-1",
                    "scan-2",
                    DetectorKind.POLICE,
                    (clean,),
                    (),
                    False,
                    harness.clock.now(),
                ),
            )
        assert outcome.findings_inserted == 1
        async with harness.db.sessions() as session:
            row: FindingRow = list((await session.scalars(select(FindingRow))).all())[0]
        assert row.fingerprint != clean.external_fingerprint
        assert_no_leaks(f"{row.location} {row.masked_snippet} {row.fingerprint}", "finding")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_recorder_rejects_raw_values_before_persistence() -> None:
    """Prevent direct callers from bypassing the resource scanner's masking."""
    harness: IssueHarness = await make_issue_harness()
    sample: Sample = github_token()
    try:
        finding: MaskedFinding = masked_finding(harness, RuleClass.TOKEN, "synthetic-token")
        safe: ResourceScan = resource_scan(harness, (finding,), "scan-unsafe")
        unsafe: ResourceScan = ResourceScan(
            safe.source,
            safe.resource_key,
            safe.display_locator,
            sample.value,
            safe.collector_config_id,
            safe.scan_job_id,
            safe.ref_json,
            safe.findings,
            safe.complete,
            safe.sweep_covered,
            safe.observed_at,
        )
        async with harness.db.sessions.begin() as session:
            with pytest.raises(TelescreenError):
                await harness.service.record_scan(session, unsafe)
        async with harness.db.sessions() as session:
            issue_count: int = len((await session.scalars(select(IssueRow))).all())
            finding_count: int = len((await session.scalars(select(FindingRow))).all())
        assert issue_count == 0
        assert finding_count == 0
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_scanner_does_not_persist_raw_sample_in_issue_rows() -> None:
    """A real detection pass persists only masked findings and safe locators."""
    harness: IssueHarness = await make_issue_harness()
    sample: Sample = github_token()
    try:
        detector: RuleDetector = RuleDetector()
        indexer: RecordingIndexer = RecordingIndexer([], [])
        scanner: ResourceScannerImpl = ResourceScannerImpl(
            harness.db.sessions,
            detector,
            lambda kinds: detector,
            Masker(harness.fingerprinter, harness.redactor),
            harness.fingerprinter,
            harness.redactor,
            harness.service,
            indexer,
            harness.clock,
            4,
            10000,
        )
        content: ResourceContent = ResourceContent(
            "custom:test",
            "collector-1",
            (Segment("segment:1", f"token {sample.value}"),),
            True,
            False,
            "resource:1",
            "sample title",
        )
        outcome: RecordOutcome = await scanner.scan_resource(content, "scan-1")
        assert outcome.findings_inserted > 0
        assert len(indexer.issue_ids) == 1
        assert len(indexer.finding_ids) == outcome.findings_inserted
        async with harness.db.sessions() as session:
            issues: list[IssueRow] = list((await session.scalars(select(IssueRow))).all())
            findings: list[FindingRow] = list((await session.scalars(select(FindingRow))).all())
            history: list[IssueHistoryRow] = list(
                (await session.scalars(select(IssueHistoryRow))).all()
            )
        for row in issues:
            assert_no_leaks(
                f"{row.display_locator} {row.display_title} {row.resource_ref_json or ''}",
                "issue row",
            )
        for row in findings:
            assert_no_leaks(
                f"{row.display_locator} {row.location} {row.masked_snippet} {row.fingerprint}",
                "finding row",
            )
        for row in history:
            assert_no_leaks(
                f"{row.actor_ref} {row.from_value or ''} {row.to_value or ''} {row.note or ''}",
                "history row",
            )
        assert_no_leaks(repr(outcome), "scan response")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_issue_api_filters_stats_and_redacts_comments() -> None:
    """Expose safe issue data and redact free-form comments at API boundaries."""
    harness: IssueHarness = await make_issue_harness()
    sample: Sample = github_token()
    try:
        finding: MaskedFinding = masked_finding(harness, RuleClass.EMAIL, "email-one")
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(session, resource_scan(harness, (finding,), "scan-1"))
        async with harness.db.sessions.begin() as session:
            token_service: TokenService = TokenService(harness.audit, harness.clock)
            api_token: CreatedToken = await token_service.create(
                session, "issues-test", frozenset((ApiScope.READ, ApiScope.WRITE)), None
            )
        ctx: ApiContext = ApiContext(harness.db.sessions, harness.clock, harness.audit)
        app: FastAPI = FastAPI()
        install_error_handlers(app)
        app.include_router(
            build_issues_router(IssuesRouterDeps(ctx, harness.service, RecordingIndexer([], [])))
        )
        transport: ASGITransport = ASGITransport(app=app)
        async with harness.db.sessions() as session:
            issue_id: str = list((await session.scalars(select(IssueRow))).all())[0].id
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers: Headers = Headers({"Authorization": f"Bearer {api_token.token}"})
            listed: HttpxResponse = await client.get(
                "/api/issues?status=unresolved&sort=first_seen", headers=headers
            )
            assert listed.status_code == 200
            assert b'"short_id":"TELE-1"' in listed.content
            assert b'"status":"unresolved"' in listed.content

            stats: HttpxResponse = await client.get(
                "/api/issues/stats?bucket=day&days=3", headers=headers
            )
            assert stats.status_code == 200
            stats_value: JsonValue = parse_json(stats.content)
            buckets: JsonValue | None = stats_value.get("buckets")
            assert buckets is not None and buckets.kind is JsonKind.ARRAY
            assert len(buckets.items) == 3
            counts: list[int] = []
            for bucket_value in buckets.items:
                count_value: JsonValue | None = bucket_value.get("count")
                assert count_value is not None
                count: int | None = count_value.as_int()
                assert count is not None
                counts.append(count)
            assert sum(counts) == 1

            comment: HttpxResponse = await client.post(
                f"/api/issues/{issue_id}/comments",
                headers=headers,
                json={"body": f"Please remove {sample.value}"},
            )
            assert comment.status_code == 201
            assert_no_leaks(comment.text, "comment response")

            history_first: HttpxResponse = await client.get(
                f"/api/issues/{issue_id}/history?limit=1", headers=headers
            )
            assert history_first.status_code == 200
            assert_no_leaks(history_first.text, "history first page")
            first_page: JsonValue = parse_json(history_first.content)
            cursor_value: JsonValue | None = first_page.get("next_cursor")
            assert cursor_value is not None
            history_cursor: str | None = cursor_value.as_str()
            assert history_cursor is not None
            history_second: HttpxResponse = await client.get(
                f"/api/issues/{issue_id}/history?limit=1&cursor={history_cursor}",
                headers=headers,
            )
            assert history_second.status_code == 200
            assert_no_leaks(history_second.text, "history second page")
            assert b'"kind":"commented"' in history_first.content + history_second.content
        async with harness.db.sessions() as session:
            history_rows: list[IssueHistoryRow] = list(
                (await session.scalars(select(IssueHistoryRow))).all()
            )
        for row in history_rows:
            assert_no_leaks(row.note or "", "comment history row")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_ingest_routes_reject_unknown_and_raw_payloads_without_echo() -> None:
    """Keep custom push failures static and expose only closed selector JSON."""
    harness: IssueHarness = await make_issue_harness()
    sample: Sample = github_token()
    try:
        selector: CustomSelector = CustomSelector(json_object((("mode", json_str("test")),)))
        config: CollectorConfig = CollectorConfig(
            "collector-1",
            "demo",
            CollectorKind.CUSTOM,
            TriggerMode.BOTH,
            None,
            None,
            1,
            selector,
            None,
            True,
        )
        configs: FakeCollectorConfigReader = FakeCollectorConfigReader(config)
        detector: RuleDetector = RuleDetector()

        def detector_factory(kinds: tuple[DetectorKind, ...]) -> Detector:
            """Return the real rule detector for this custom source."""
            del kinds
            return detector

        indexer: RecordingIndexer = RecordingIndexer([], [])
        scanner: ResourceScannerImpl = ResourceScannerImpl(
            harness.db.sessions,
            detector,
            detector_factory,
            Masker(harness.fingerprinter, harness.redactor),
            harness.fingerprinter,
            harness.redactor,
            harness.service,
            indexer,
            harness.clock,
            8,
            10000,
        )
        ingest: IngestService = IngestService(
            harness.db.sessions,
            scanner,
            harness.service,
            configs,
            FakeScanJobStore(),
            indexer,
            harness.clock,
        )
        async with harness.db.sessions.begin() as session:
            token_service: TokenService = TokenService(harness.audit, harness.clock)
            api_token: CreatedToken = await token_service.create(
                session, "ingest-test", frozenset((ApiScope.INGEST,)), None
            )
        ctx: ApiContext = ApiContext(harness.db.sessions, harness.clock, harness.audit)
        app: FastAPI = FastAPI()
        install_error_handlers(app)
        app.include_router(build_ingest_router(IngestRouterDeps(ctx, ingest, configs)))
        transport: ASGITransport = ASGITransport(app=app)
        headers: Headers = Headers({"Authorization": f"Bearer {api_token.token}"})
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            unknown: HttpxResponse = await client.post(
                "/api/ingest/v1/scan",
                headers=headers,
                json={"source": "custom:missing", "resources": []},
            )
            assert unknown.status_code == 404
            assert "missing" not in unknown.text

            rejected: HttpxResponse = await client.post(
                "/api/ingest/v1/findings",
                headers=headers,
                json={
                    "source": "custom:demo",
                    "locator": "repo/file",
                    "complete": False,
                    "findings": [
                        {
                            "locator": "repo/file",
                            "rule_id": "token.github",
                            "rule_class": RuleClass.TOKEN.value,
                            "masked_snippet": sample.value,
                            "location": "line 1",
                            "confidence": 0.9,
                            "external_fingerprint": "external-fingerprint",
                        }
                    ],
                },
            )
            assert rejected.status_code == 422
            assert_no_leaks(rejected.text, "ingest error response")

            accepted: HttpxResponse = await client.post(
                "/api/ingest/v1/scan",
                headers=headers,
                json={
                    "source": "custom:demo",
                    "resources": [
                        {
                            "locator": "repo/file",
                            "title": "Sample repository file",
                            "segments": [{"locator": "line:1", "text": f"token {sample.value}"}],
                        }
                    ],
                },
            )
            assert accepted.status_code == 200
            assert b'"findings": 1' in accepted.content
            assert_no_leaks(accepted.text, "accepted ingest response")

            source_config: HttpxResponse = await client.get(
                "/api/ingest/v1/sources/demo/config", headers=headers
            )
            assert source_config.status_code == 200
            assert source_config.content == b'{"mode":"test"}'
        async with harness.db.sessions() as session:
            issue_rows: list[IssueRow] = list((await session.scalars(select(IssueRow))).all())
            finding_rows: list[FindingRow] = list((await session.scalars(select(FindingRow))).all())
            history_rows: list[IssueHistoryRow] = list(
                (await session.scalars(select(IssueHistoryRow))).all()
            )
        assert len(issue_rows) == 1
        assert len(finding_rows) == 1
        for row in issue_rows:
            assert_no_leaks(
                " ".join(
                    (
                        row.id,
                        row.grouping_key,
                        row.source,
                        row.resource_key,
                        row.display_locator,
                        row.resource_ref_json or "",
                        row.collector_config_id or "",
                        row.display_title,
                        row.rule_class,
                        row.severity,
                        row.status,
                        row.assignee_member_id or "",
                        row.assignee_team_id or "",
                        row.resolution or "",
                    )
                ),
                "ingested issue row",
            )
        for row in finding_rows:
            assert_no_leaks(
                " ".join(
                    (
                        row.id,
                        row.issue_id,
                        row.scan_job_id or "",
                        row.source,
                        row.resource_key,
                        row.display_locator,
                        row.location,
                        row.rule_id,
                        row.rule_class,
                        row.detector,
                        row.masked_snippet,
                        row.fingerprint,
                        row.fingerprint_key_id,
                    )
                ),
                "ingested finding row",
            )
        for row in history_rows:
            assert_no_leaks(
                f"{row.actor_ref} {row.from_value or ''} {row.to_value or ''} {row.note or ''}",
                "ingested history row",
            )
    finally:
        await harness.close()
