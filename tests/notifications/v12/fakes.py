"""Shared fakes for the v1.2 notification template and sender tests."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import DlpPolicy, ErrorCode, RuleClass, Severity, Visibility
from telescreen.db.models import IssueRow
from telescreen.detection import RuleDetector
from telescreen.masking.redact import Redactor
from telescreen.notifications.dlp import DlpGate
from telescreen.notifications.repository import NotificationRepository
from telescreen.notifications.sender import NotificationService
from telescreen.notifications.templates.repository import TemplateRepository
from telescreen.notifications.templates.resolver import DbTemplateResolver
from telescreen.notifications.templates.service import TemplateService
from telescreen.ports.audit import AuditEntry
from telescreen.ports.directory import AssigneeIds, ResolvedRecipients
from telescreen.ports.workflow import TaskSpec


@dataclass
class RecordingAudit:
    """Collect audit entries in memory."""

    entries: list[AuditEntry] = field(default_factory=list)

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Record one audit entry."""
        del session
        self.entries.append(entry)
        return len(self.entries)


@dataclass
class RecordingQueue:
    """Collect enqueued task specifications."""

    specs: list[TaskSpec] = field(default_factory=list)

    async def enqueue(self, session: AsyncSession, spec: TaskSpec) -> bool:
        """Record a task spec."""
        del session
        self.specs.append(spec)
        return True

    async def cancel_by_ref(self, session: AsyncSession, ref_id: str) -> int:
        """Implement the unused queue operation."""
        del session, ref_id
        return 0


EMAIL_ONLY: ResolvedRecipients = ResolvedRecipients(("alice@example.test",), (), (), ("m-1",))
SLACK_ONLY: ResolvedRecipients = ResolvedRecipients((), (), ("U12345678",), ("m-2",))
EVERYWHERE: ResolvedRecipients = ResolvedRecipients(
    ("team@example.test",), ("C12345678",), ("U87654321",), ()
)


@dataclass
class MapResolver:
    """Resolve fixed refs; any other ref is unknown."""

    known: tuple[tuple[str, ResolvedRecipients], ...] = (
        ("member:alice", EMAIL_ONLY),
        ("member:bob", SLACK_ONLY),
        ("team:security", EVERYWHERE),
        ("email:x@example.test", ResolvedRecipients(("x@example.test",), (), (), ())),
    )

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients:
        """Return the fixed recipients of a ref or raise recipient_unknown."""
        del session
        for known_ref, recipients in self.known:
            if known_ref == ref:
                return recipients
        raise TelescreenError(ErrorCode.RECIPIENT_UNKNOWN, "recipient reference is unknown")

    async def is_member_of(self, session: AsyncSession, member_id: str, ref: str) -> bool:
        """Implement the unused membership operation."""
        del session, member_id, ref
        return False

    async def assignee_of(self, session: AsyncSession, ref: str) -> AssigneeIds:
        """Implement the unused assignee operation."""
        del session, ref
        return AssigneeIds(None, None)


@dataclass
class RecordingSmtp:
    """Capture email messages passed to the SMTP transport."""

    messages: list[EmailMessage] = field(default_factory=list)

    def send(self, message: EmailMessage) -> None:
        """Record one message."""
        self.messages.append(message)


def make_gate(policy: DlpPolicy = DlpPolicy.MASK) -> DlpGate:
    """Create the DLP gate with the real rule detector and redactor."""
    return DlpGate(RuleDetector(), Redactor.default(), policy)


def make_templates(audit: RecordingAudit, clock: FakeClock) -> TemplateService:
    """Create a template service over the real repository and gate."""
    return TemplateService(TemplateRepository(), Redactor.default(), make_gate(), audit, clock)


def make_sender(
    queue: RecordingQueue,
    audit: RecordingAudit,
    clock: FakeClock,
    policy: DlpPolicy = DlpPolicy.MASK,
    resolver: MapResolver | None = None,
) -> NotificationService:
    """Create the notification service with the DB template resolver."""
    return NotificationService(
        make_gate(policy),
        resolver or MapResolver(),
        queue,
        audit,
        clock,
        NotificationRepository(),
        DbTemplateResolver(),
    )


def make_issue(number: int) -> IssueRow:
    """Build an issue row with masked display fields and enum columns."""
    now: datetime = datetime(2026, 10, 10, 12, tzinfo=UTC)
    return IssueRow(
        id=f"issue-{number}",
        number=number,
        grouping_key=f"group-{number}",
        source="github",
        resource_key=f"resource-{number}",
        display_locator="masked-resource",
        resource_ref_json=None,
        collector_config_id=None,
        display_title="safe title fixture",
        rule_class=RuleClass.TOKEN.value,
        severity=Severity.HIGH.value,
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
        exposure_visibility=Visibility.PUBLIC.value,
        risk_score=72,
    )
