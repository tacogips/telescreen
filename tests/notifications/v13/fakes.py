"""Fakes and row factories shared by v1.3 notification tests."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode
from telescreen.db.models import MemberRow
from telescreen.ports.audit import AuditEntry
from telescreen.ports.directory import AssigneeIds, RecipientResolver, ResolvedRecipients
from telescreen.ports.workflow import TaskSpec


@dataclass
class RecordingAudit:
    """Capture audit entries without opening another transaction."""

    entries: list[AuditEntry] = field(default_factory=list)

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Record one audit entry."""
        del session
        self.entries.append(entry)
        return len(self.entries)


@dataclass
class RecordingQueue:
    """Capture queued task specs for assertions."""

    specs: list[TaskSpec] = field(default_factory=list)

    async def enqueue(self, session: AsyncSession, spec: TaskSpec) -> bool:
        """Record a task spec and report that it was enqueued."""
        del session
        if any(saved.idempotency_key == spec.idempotency_key for saved in self.specs):
            return False
        self.specs.append(spec)
        return True

    async def cancel_by_ref(self, session: AsyncSession, ref_id: str) -> int:
        """Implement the unused queue cancellation method."""
        del session, ref_id
        return 0


@dataclass(frozen=True)
class StaticResolver(RecipientResolver):
    """Resolve explicitly declared recipient refs for notification tests."""

    entries: tuple[tuple[str, ResolvedRecipients], ...]

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients:
        """Return a configured recipient or a safe unknown-reference error."""
        del session
        for known_ref, recipients in self.entries:
            if ref == known_ref:
                return recipients
        raise TelescreenError(ErrorCode.RECIPIENT_UNKNOWN, "recipient reference is unknown")

    async def is_member_of(self, session: AsyncSession, member_id: str, ref: str) -> bool:
        """Implement the unused team membership operation."""
        del session, member_id, ref
        return False

    async def assignee_of(self, session: AsyncSession, ref: str) -> AssigneeIds:
        """Implement the unused assignee lookup."""
        del session, ref
        return AssigneeIds(None, None)


def member_row(
    member_id: str,
    handle: str,
    email: str | None,
    slack_user_id: str | None,
    active: bool = True,
) -> MemberRow:
    """Build a persisted member row with stable test timestamps."""
    now: datetime = datetime(2026, 10, 12, tzinfo=UTC)
    return MemberRow(
        id=member_id,
        handle=handle,
        display_name=handle.title(),
        email=email,
        slack_user_id=slack_user_id,
        active=active,
        created_at=now,
        updated_at=now,
        role="responder",
    )
