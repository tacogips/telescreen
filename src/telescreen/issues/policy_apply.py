"""Policy re-evaluation task and hourly issue risk maintenance."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock
from telescreen.core.ids import new_id
from telescreen.core.vocabulary import (
    OPEN_STATUSES,
    AssetTier,
    EngineTaskKind,
    IssueEventKind,
    IssueHistoryKind,
    IssueStatus,
    PolicyEffect,
    RuleClass,
    Severity,
    Visibility,
)
from telescreen.db.models import IssueRow
from telescreen.issues.recording import decide_policy, placement_keys, score_risk
from telescreen.issues.repository import IssueRepository
from telescreen.issues.service import IssueService
from telescreen.ports.issues import IssueEvent
from telescreen.ports.policy import PolicyDecision
from telescreen.ports.search import SearchIndexer
from telescreen.ports.workflow import ClaimedTask, ScheduleProvider, TaskHandler, TaskResult


async def reopen_allowed_issue(
    service: IssueService,
    session: AsyncSession,
    issue: IssueRow,
    decision: PolicyDecision,
    now: datetime,
) -> None:
    """Apply the shared allowed-to-unresolved policy transition."""
    issue.status = IssueStatus.UNRESOLVED.value
    issue.resolution = None
    issue.resolved_at = None
    await service._history(
        session,
        issue.id,
        IssueHistoryKind.POLICY_REEVALUATED,
        "system:policy",
        now,
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
            RuleClass(issue.rule_class),
            Severity(issue.severity),
            now,
        ),
    )
    await service._route_owner(session, issue, decision, now)


@dataclass(frozen=True)
class PolicyReevaluateHandler(TaskHandler):
    """Apply current placement policy decisions to issues in bounded batches."""

    sessions: async_sessionmaker[AsyncSession]
    service: IssueService
    indexer: SearchIndexer
    clock: Clock
    repository: IssueRepository = IssueRepository()
    kind: EngineTaskKind = EngineTaskKind.POLICY_REEVALUATE

    async def handle(self, task: ClaimedTask) -> TaskResult:
        """Re-evaluate all eligible issues without treating policy as remediation."""
        del task
        offset: int = 0
        while True:
            changed_ids: list[str] = []
            async with self.sessions.begin() as session:
                issues: tuple[IssueRow, ...] = tuple(
                    await self.repository.reconciled_issues(session, offset, 200)
                )
                for issue in issues:
                    before: IssueStatus = IssueStatus(issue.status)
                    was_changed: bool = False
                    placement: tuple[str, ...] = placement_keys(issue.placement_keys_json)
                    decision: PolicyDecision = await decide_policy(
                        session,
                        self.service.decider,
                        issue.source,
                        RuleClass(issue.rule_class),
                        placement,
                        Visibility(issue.exposure_visibility),
                        issue.resource_modified_at or issue.first_seen,
                        self.clock.now(),
                        self.service.owner_routing,
                    )
                    if before in OPEN_STATUSES and decision.effect is PolicyEffect.ALLOW:
                        issue.status = IssueStatus.ALLOWED.value
                        issue.resolution = "policy_changed"
                        issue.resolved_at = self.clock.now()
                        was_changed = True
                        await self.service._history(
                            session,
                            issue.id,
                            IssueHistoryKind.POLICY_REEVALUATED,
                            "system:policy",
                            self.clock.now(),
                            from_value=before.value,
                            to_value=IssueStatus.ALLOWED.value,
                        )
                        await self.service._reject_requested_exceptions(
                            session, issue.id, self.clock.now()
                        )
                    elif before is IssueStatus.ALLOWED and decision.effect is PolicyEffect.VIOLATE:
                        now: datetime = self.clock.now()
                        await reopen_allowed_issue(self.service, session, issue, decision, now)
                        was_changed = True
                    if (issue.policy_name, issue.policy_version) != (
                        decision.policy_name,
                        decision.policy_version,
                    ):
                        issue.policy_name = decision.policy_name
                        issue.policy_version = decision.policy_version
                        was_changed = True
                    if was_changed:
                        issue.updated_at = self.clock.now()
                        changed_ids.append(issue.id)
            for issue_id in changed_ids:
                await self.indexer.index_issue(issue_id)
            if len(issues) < 200:
                break
            offset += 200
        return TaskResult.done()


@dataclass
class IssuePolicyMaintenance(ScheduleProvider):
    """Refresh issue risk and enforce age-based allow policies hourly."""

    service: IssueService
    repository: IssueRepository = IssueRepository()
    _last_run: datetime | None = None

    async def tick(self, session: AsyncSession, now: datetime) -> int:
        """Update risk and policy status at most once per hour per process."""
        if self._last_run is not None and now - self._last_run < timedelta(hours=1):
            return 0
        self._last_run = now
        changed_issue_ids: list[str] = []
        for issue in await self.repository.reconciled_issues(session, 0, 100000):
            visibility: Visibility = Visibility(issue.exposure_visibility)
            severity: Severity = Severity(issue.severity)
            tier: AssetTier | None = (
                AssetTier(issue.asset_tier) if issue.asset_tier is not None else None
            )
            score: int = score_risk(
                self.service.scorer, severity, visibility, tier, issue.first_seen, now
            )
            if IssueStatus(issue.status) in OPEN_STATUSES and score != issue.risk_score:
                issue.risk_score = score
                changed_issue_ids.append(issue.id)
            if issue.status == IssueStatus.ALLOWED.value and self.service.decider is not None:
                placement: tuple[str, ...] = placement_keys(issue.placement_keys_json)
                decision: PolicyDecision = await decide_policy(
                    session,
                    self.service.decider,
                    issue.source,
                    RuleClass(issue.rule_class),
                    placement,
                    visibility,
                    issue.resource_modified_at or issue.first_seen,
                    now,
                    self.service.owner_routing,
                )
                if decision.effect is PolicyEffect.VIOLATE:
                    await reopen_allowed_issue(self.service, session, issue, decision, now)
                    issue.policy_name = decision.policy_name
                    issue.policy_version = decision.policy_version
                    issue.updated_at = now
                    issue.risk_score = score_risk(
                        self.service.scorer,
                        severity,
                        visibility,
                        tier,
                        issue.first_seen,
                        now,
                    )
                    if issue.id not in changed_issue_ids:
                        changed_issue_ids.append(issue.id)
        return len(changed_issue_ids)
