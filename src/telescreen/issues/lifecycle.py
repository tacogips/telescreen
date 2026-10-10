"""Issue remediation requests, resolution, and team release."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import JsonValue, json_int, json_object, json_str, parse_json
from telescreen.core.vocabulary import (
    CREDENTIAL_CLASSES,
    OPEN_STATUSES,
    ActorKind,
    EngineTaskKind,
    ErrorCode,
    ExceptionDuration,
    IssueHistoryKind,
    IssueResolution,
    IssueStatus,
    RemediationAction,
    RuleClass,
    Visibility,
)
from telescreen.db.models import IssueExceptionRow, IssueHistoryRow, IssueRow
from telescreen.issues.exceptions import ExceptionService
from telescreen.issues.keys import short_id
from telescreen.issues.occurrences import OccurrenceResult, linked
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.directory import AssigneeIds, OwnerDirectory, RecipientResolver
from telescreen.ports.issues import RemediationReceipt
from telescreen.ports.policy import PolicyDecider, PolicyDecision, PolicySubject
from telescreen.ports.workflow import TaskQueue, TaskSpec


@dataclass
class IssueRemediationService:
    """Apply issue remediation and reasoned-resolution transitions."""

    audit: AuditLog
    clock: Clock
    redactor: Redactor
    queue: TaskQueue
    resolver: RecipientResolver
    owners: OwnerDirectory
    decider: PolicyDecider
    exceptions: ExceptionService
    default_team: str | None = None
    include_linked_classes: tuple[RuleClass, ...] = CREDENTIAL_CLASSES

    async def request_verification(
        self,
        session: AsyncSession,
        issue_id: str,
        action: RemediationAction,
        include_linked: bool,
        actor_ref: str,
        note: str | None,
    ) -> RemediationReceipt:
        """Move an eligible issue to verification and enqueue eligible rescans."""
        issue: IssueRow = await self._required(session, issue_id)
        if issue.status not in tuple(
            status.value
            for status in (
                IssueStatus.UNRESOLVED,
                IssueStatus.ACKNOWLEDGED,
                IssueStatus.IN_PROGRESS,
                IssueStatus.REGRESSED,
            )
        ):
            raise TelescreenError(ErrorCode.INVALID_TRANSITION, "invalid issue transition")
        now: datetime = self.clock.now()
        issue_ids: list[str] = [issue.id]
        if include_linked and RuleClass(issue.rule_class) in self.include_linked_classes:
            occurrences: OccurrenceResult = await linked(session, issue.id)
            for occurrence in occurrences.items:
                candidate: IssueRow | None = await session.get(IssueRow, occurrence.issue_id)
                if candidate is not None and candidate.status in _REMEDIATION_STATUSES:
                    issue_ids.append(candidate.id)
        enqueued: int = 0
        for current_id in issue_ids:
            current: IssueRow = await self._required(session, current_id)
            previous: str = current.status
            safe_note: str | None = self.redactor.redact(note).text[:4000] if note else None
            if current_id != issue.id:
                safe_note = f"linked to {short_id(issue.number)}"
            current.status = IssueStatus.PENDING_VERIFICATION.value
            current.resolved_at = None
            current.resolution = None
            current.updated_at = now
            history_id: str = new_id()
            session.add(
                IssueHistoryRow(
                    id=history_id,
                    issue_id=current.id,
                    kind=IssueHistoryKind.VERIFICATION_REQUESTED.value,
                    actor_ref=actor_ref[:256],
                    note=safe_note,
                    from_value=previous,
                    to_value=action.value,
                    at=now,
                )
            )
            await self._audit(
                session,
                "issue.remediation_requested",
                current.id,
                actor_ref,
                1,
            )
            if current.resource_ref_json is not None and current.collector_config_id is not None:
                enqueued += int(
                    await self.queue.enqueue(
                        session,
                        TaskSpec(
                            EngineTaskKind.VERIFY_RESCAN,
                            current.id,
                            json_object((("issue_id", json_str(current.id)),)),
                            now,
                            f"verify:{current.id}:{history_id}",
                        ),
                    )
                )
        return RemediationReceipt(tuple(issue_ids), enqueued)

    async def resolve_with_reason(
        self,
        session: AsyncSession,
        issue_id: str,
        reason: IssueResolution,
        note: str,
        actor_ref: str,
        is_admin: bool,
    ) -> None:
        """Resolve an issue with a required note and an explicit reason."""
        if reason not in (IssueResolution.FALSE_POSITIVE, IssueResolution.MANUAL_UNVERIFIED):
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid resolution reason")
        if reason is IssueResolution.MANUAL_UNVERIFIED and not is_admin:
            raise TelescreenError(ErrorCode.FORBIDDEN, "administrator permission required")
        safe_note: str = self.redactor.redact(note).text[:4000]
        if not note.strip() or not safe_note.strip():
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "resolution note required")
        issue: IssueRow = await self._required(session, issue_id)
        if issue.status not in tuple(status.value for status in OPEN_STATUSES):
            raise TelescreenError(ErrorCode.INVALID_TRANSITION, "invalid issue transition")
        now: datetime = self.clock.now()
        await self._reject_requested_exceptions(session, issue.id, now)
        previous: str = issue.status
        issue.status = IssueStatus.RESOLVED.value
        issue.resolution = reason.value
        issue.resolved_at = now
        issue.updated_at = now
        session.add(
            IssueHistoryRow(
                id=new_id(),
                issue_id=issue.id,
                kind=IssueHistoryKind.STATUS_CHANGED.value,
                actor_ref=actor_ref[:256],
                note=safe_note,
                from_value=previous,
                to_value=IssueStatus.RESOLVED.value,
                at=now,
            )
        )
        await self._audit(session, "issue.resolved", issue.id, actor_ref, 1)

    async def release_to_team(self, session: AsyncSession, issue_id: str, actor_ref: str) -> None:
        """Clear a member assignment and assign the policy or default team."""
        issue: IssueRow = await self._required(session, issue_id)
        issue.assignee_member_id = None
        issue.assignee_team_id = None
        decision: PolicyDecision = await self.decider.decide(
            session, self._subject(issue), self.clock.now()
        )
        team: str | None = decision.owner_team or self.default_team
        assignment: str = "none"
        if team is not None:
            ids: AssigneeIds = await self.resolver.assignee_of(session, f"team:{team}")
            issue.assignee_member_id = ids.member_id
            issue.assignee_team_id = ids.team_id
            assignment = f"team:{team}"
        now: datetime = self.clock.now()
        issue.updated_at = now
        session.add(
            IssueHistoryRow(
                id=new_id(),
                issue_id=issue.id,
                kind=IssueHistoryKind.ASSIGNED.value,
                actor_ref=actor_ref[:256],
                to_value=assignment,
                at=now,
            )
        )
        await self._audit(session, "issue.assigned", issue.id, actor_ref, 0)

    async def request_exception(
        self,
        session: AsyncSession,
        issue_id: str,
        reason: str,
        duration: ExceptionDuration,
        actor_ref: str,
        is_admin: bool,
    ) -> str:
        """Delegate exception creation to the exception lifecycle service."""
        return await self.exceptions.request(
            session, issue_id, reason, duration, actor_ref, is_admin
        )

    async def _required(self, session: AsyncSession, issue_id: str) -> IssueRow:
        """Return a required issue or a safe not-found error."""
        issue: IssueRow | None = await session.get(IssueRow, issue_id)
        if issue is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
        return issue

    async def _reject_requested_exceptions(
        self, session: AsyncSession, issue_id: str, now: datetime
    ) -> None:
        """Close pending requests when the issue leaves open status."""
        rows: tuple[IssueExceptionRow, ...] = tuple(
            (
                await session.scalars(
                    select(IssueExceptionRow).where(
                        IssueExceptionRow.issue_id == issue_id,
                        IssueExceptionRow.status == "requested",
                    )
                )
            ).all()
        )
        for row in rows:
            row.status = "rejected"
            row.decided_by = "system"
            row.decided_at = now
            session.add(
                IssueHistoryRow(
                    id=new_id(),
                    issue_id=issue_id,
                    kind=IssueHistoryKind.EXCEPTION_REJECTED.value,
                    actor_ref="system:issues",
                    to_value="system",
                    at=now,
                )
            )
            await self._audit(session, "issue.exception_rejected", issue_id, "system:issues", 1)

    async def _audit(
        self,
        session: AsyncSession,
        action: str,
        issue_id: str,
        actor_ref: str,
        changed: int,
    ) -> None:
        """Write count-only audit details for an issue transition."""
        details: JsonValue = json_object((("changed", json_int(changed)),))
        kind: ActorKind = ActorKind.MEMBER if actor_ref.startswith("member:") else ActorKind.TOKEN
        await self.audit.append(
            session,
            AuditEntry(kind, actor_ref[:256], action, "issue", issue_id, "Issue updated", details),
        )

    @staticmethod
    def _subject(issue: IssueRow) -> PolicySubject:
        """Build a policy subject using stored keyed placement references only."""
        placements: tuple[str, ...] = ()
        if issue.placement_keys_json is not None:
            parsed: JsonValue = parse_json(issue.placement_keys_json)
            array_values: tuple[JsonValue, ...] | None = parsed.as_list()
            if array_values is not None:
                placement_values: list[str] = []
                for value in array_values:
                    text: str | None = value.as_str()
                    if text is not None:
                        placement_values.append(text)
                placements = tuple(placement_values)
        return PolicySubject(
            issue.source,
            RuleClass(issue.rule_class),
            placements,
            Visibility(issue.exposure_visibility),
            issue.resource_modified_at or issue.first_seen,
        )


_REMEDIATION_STATUSES: tuple[str, ...] = tuple(
    status.value
    for status in (
        IssueStatus.UNRESOLVED,
        IssueStatus.ACKNOWLEDGED,
        IssueStatus.IN_PROGRESS,
        IssueStatus.REGRESSED,
    )
)
