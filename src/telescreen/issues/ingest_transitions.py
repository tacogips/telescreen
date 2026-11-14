"""Post-insert issue state transitions performed during resource ingestion."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.ids import new_id
from telescreen.core.vocabulary import (
    OPEN_STATUSES,
    IssueEventKind,
    IssueHistoryKind,
    IssueResolution,
    IssueStatus,
    RuleClass,
    Severity,
)
from telescreen.db.models import IssueExceptionRow, IssueRow
from telescreen.ports.issues import IssueActivity, IssueEvent
from telescreen.ports.policy import PolicyDecision

if TYPE_CHECKING:
    from telescreen.issues.service import IssueService


async def apply_post_insert_transitions(
    service: IssueService,
    session: AsyncSession,
    issue: IssueRow,
    created: bool,
    previous_status: IssueStatus,
    decision: PolicyDecision,
    previous_policy: tuple[str | None, int | None],
    regressed_fingerprint: bool,
    current_severity: Severity,
    rule_class: RuleClass,
    observed_at: datetime,
) -> int:
    """Apply the existing regression, exception, verification, and policy chain."""
    regressed: int = 0
    if (
        not created
        and previous_status is IssueStatus.RESOLVED
        and decision.effect.value == "violate"
    ):
        issue.status = IssueStatus.REGRESSED.value
        issue.resolved_at = None
        issue.resolution = None
        issue.regression_count += 1
        issue.needs_review = True
        regressed = 1
        await service._history(
            session,
            issue.id,
            IssueHistoryKind.REGRESSED,
            "system:scanner",
            observed_at,
            from_value=IssueStatus.RESOLVED.value,
            to_value=IssueStatus.REGRESSED.value,
        )
        await service._audit(session, "issue.regressed", "issue", issue.id, "Issue regressed", 1, 0)
        await service.events.on_issue_event(
            session,
            IssueEvent(
                IssueEventKind.ISSUE_REGRESSED,
                new_id(),
                issue.id,
                issue.source,
                rule_class,
                Severity(issue.severity),
                observed_at,
            ),
        )
        await service.activity.on_activity(
            session,
            IssueActivity(IssueHistoryKind.REGRESSED, issue.id, "system:scanner", observed_at),
        )
    elif not created and previous_status is IssueStatus.IGNORED and regressed_fingerprint:
        exception: (
            IssueExceptionRow | None
        ) = await service.repository.active_until_regressed_exception(session, issue.id)
        if exception is not None:
            exception.status = "expired"
            exception.decided_by = "system"
            exception.decided_at = observed_at
            issue.status = IssueStatus.REGRESSED.value
            issue.needs_review = True
            issue.regression_count += 1
            regressed = 1
            await service._history(
                session,
                issue.id,
                IssueHistoryKind.EXCEPTION_EXPIRED,
                "system:scanner",
                observed_at,
                from_value="active",
                to_value="expired",
            )
            await service._history(
                session,
                issue.id,
                IssueHistoryKind.REGRESSED,
                "system:scanner",
                observed_at,
                from_value=IssueStatus.IGNORED.value,
                to_value=IssueStatus.REGRESSED.value,
            )
            await service.events.on_issue_event(
                session,
                IssueEvent(
                    IssueEventKind.ISSUE_REGRESSED,
                    new_id(),
                    issue.id,
                    issue.source,
                    rule_class,
                    current_severity,
                    observed_at,
                ),
            )
    elif (
        not created
        and previous_status is IssueStatus.PENDING_VERIFICATION
        and decision.effect.value == "violate"
    ):
        issue.status = IssueStatus.UNRESOLVED.value
        issue.needs_review = True
        issue.resolution = None
        issue.resolved_at = None
        await service._history(
            session,
            issue.id,
            IssueHistoryKind.VERIFICATION_FAILED,
            "system:scanner",
            observed_at,
            from_value=previous_status.value,
            to_value=IssueStatus.UNRESOLVED.value,
        )
    elif (
        not created
        and previous_status is IssueStatus.ALLOWED
        and decision.effect.value == "violate"
    ):
        issue.status = IssueStatus.UNRESOLVED.value
        issue.resolution = None
        await service._history(
            session,
            issue.id,
            IssueHistoryKind.POLICY_REEVALUATED,
            "system:policy",
            observed_at,
            from_value=IssueStatus.ALLOWED.value,
            to_value=IssueStatus.UNRESOLVED.value,
        )
        await service.events.on_issue_event(
            session,
            IssueEvent(
                IssueEventKind.ISSUE_CREATED,
                new_id(),
                issue.id,
                issue.source,
                rule_class,
                current_severity,
                observed_at,
            ),
        )
        await service._route_owner(session, issue, decision, observed_at)
    elif decision.effect.value == "allow" and previous_status in OPEN_STATUSES:
        if previous_status is IssueStatus.ARCHIVED:
            from telescreen.issues.archive import clear_archive

            clear_archive(issue)
        issue.status = IssueStatus.ALLOWED.value
        issue.needs_review = False
        issue.resolution = (
            IssueResolution.POLICY_CHANGED.value
            if previous_policy != (decision.policy_name, decision.policy_version)
            else IssueResolution.EXPOSURE_REDUCED.value
        )
        issue.resolved_at = observed_at
        await service._reject_requested_exceptions(session, issue.id, observed_at)
    elif (
        not created and previous_status is IssueStatus.ALLOWED and decision.effect.value == "allow"
    ):
        issue.status = IssueStatus.ALLOWED.value
    elif (
        not created and previous_status is IssueStatus.RESOLVED and decision.effect.value == "allow"
    ):
        issue.status = IssueStatus.ALLOWED.value
        issue.resolution = IssueResolution.POLICY_CHANGED.value
        issue.resolved_at = observed_at
        await service._reject_requested_exceptions(session, issue.id, observed_at)
    return regressed


async def resolve_auto(
    service: IssueService,
    session: AsyncSession,
    issue: IssueRow,
    at: datetime,
    resolution: IssueResolution = IssueResolution.VERIFIED_REMOVED,
) -> None:
    """Apply automatic resolution with history, audit, and request cleanup."""
    previous: str = issue.status
    if previous == IssueStatus.ARCHIVED.value:
        from telescreen.issues.archive import clear_archive

        clear_archive(issue)
    issue.status = IssueStatus.RESOLVED.value
    issue.resolution = resolution.value
    issue.resolved_at = at
    issue.updated_at = at
    await service._history(
        session,
        issue.id,
        IssueHistoryKind.AUTO_RESOLVED,
        "system:scanner",
        at,
        from_value=previous,
        to_value=IssueStatus.RESOLVED.value,
    )
    await service._audit(
        session, "issue.auto_resolved", "issue", issue.id, "Issue auto-resolved", 1, 0
    )
    await service._reject_requested_exceptions(session, issue.id, at)
