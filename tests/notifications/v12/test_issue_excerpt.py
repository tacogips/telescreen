"""IssueService snapshots carry the latest finding's masked snippet as the excerpt."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import FakeClock
from telescreen.core.vocabulary import RuleClass
from telescreen.db.models import FindingRow
from telescreen.issues.service import IssueService
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.issues import IssueEvent, IssueSnapshot
from tests.notifications.v12.fakes import MapResolver, RecordingAudit, make_issue
from tests.support.db import SqliteHarness, make_sqlite_harness

OBSERVED: datetime = datetime(2026, 10, 10, 12, tzinfo=UTC)


@dataclass
class RecordingEvents:
    """Collect issue events."""

    events: list[IssueEvent] = field(default_factory=list)

    async def on_issue_event(self, session: AsyncSession, event: IssueEvent) -> None:
        """Record one event."""
        del session
        self.events.append(event)


def finding(finding_id: str, snippet: str, observed_at: datetime) -> FindingRow:
    """Build a masked finding of issue-1."""
    return FindingRow(
        id=finding_id,
        issue_id="issue-1",
        scan_job_id=None,
        source="github",
        resource_key="resource-1",
        display_locator="masked-resource",
        location="segment:1",
        rule_id="github_token",
        rule_class=RuleClass.TOKEN.value,
        detector="rule",
        confidence=0.9,
        masked_snippet=snippet,
        fingerprint=f"fp-{finding_id}",
        fingerprint_key_id="key-1",
        observed_at=observed_at,
    )


async def test_snapshot_excerpt_is_latest_masked_snippet() -> None:
    """The newest finding's masked snippet fills masked_excerpt; none gives None."""
    harness: SqliteHarness = await make_sqlite_harness()
    service: IssueService = IssueService(
        RecordingAudit(),
        RecordingEvents(),
        Fingerprinter(b"k" * 32, "test"),
        Redactor.default(),
        MapResolver(),
        FakeClock(OBSERVED),
    )
    async with harness.sessions.begin() as session:
        session.add(make_issue(1))
    async with harness.sessions() as session:
        empty: IssueSnapshot | None = await service.get(session, "issue-1")
    async with harness.sessions.begin() as session:
        session.add(finding("f-old", "old = ****", OBSERVED - timedelta(days=1)))
        session.add(finding("f-new", 'token = "****"', OBSERVED))
    async with harness.sessions() as session:
        snapshot: IssueSnapshot | None = await service.get(session, "issue-1")
    assert empty is not None and empty.masked_excerpt is None
    assert snapshot is not None and snapshot.masked_excerpt == 'token = "****"'
    await harness.close()
