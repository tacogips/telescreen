"""Issue grouping, persistence, lifecycle transitions, and audit events."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import JsonValue, dump_json, json_array, json_int, json_object, json_str
from telescreen.core.vocabulary import (
    OPEN_STATUSES,
    RECONCILED_STATUSES,
    ActorKind,
    AssetTier,
    ErrorCode,
    IssueEventKind,
    IssueHistoryKind,
    IssuePriority,
    IssueResolution,
    IssueStatus,
    RuleClass,
    Severity,
    Visibility,
    priority_for_severity,
    severity_for,
)
from telescreen.db.models import FindingRow, IssueExceptionRow, IssueHistoryRow, IssueRow
from telescreen.issues.archive import (
    ArchivePolicy,
    clear_archive,
    evaluate_after_ingest,
)
from telescreen.issues.exposure import (
    effective_severity,
    exposure_json,
    merge_visibility,
    severity_floor,
)
from telescreen.issues.groups import (
    GroupAction,
    GroupChange,
    cascade_to_children,
    primary_of,
    propagate_child_open,
    recompute_group_counters,
)
from telescreen.issues.ingest_transitions import apply_post_insert_transitions, resolve_auto
from telescreen.issues.keys import grouping_key, short_id
from telescreen.issues.recording import (
    decide_policy,
    highest_asset_tier,
    score_risk,
)
from telescreen.issues.recording import (
    placement_keys as decode_placement_keys,
)
from telescreen.issues.repository import IssueRepository
from telescreen.issues.routing import IssueOwnerRouter
from telescreen.issues.transitions import ensure_user_transition
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.directory import AssigneeIds, OwnerDirectory, RecipientResolver
from telescreen.ports.findings import ExternalScan, MaskedFinding, RecordOutcome, ResourceScan
from telescreen.ports.inventory import (
    InventoryObserver,
    NoopInventoryObserver,
    ResourceObservation,
    ValueFingerprint,
)
from telescreen.ports.issues import (
    FindingsObserver,
    FindingsRecorded,
    IssueActivity,
    IssueActivityNotifier,
    IssueEvent,
    IssueEventSink,
    IssueHistoryEntry,
    IssueSnapshot,
    NoopFindingsObserver,
    NoopIssueActivityNotifier,
    OpenResourceRef,
    ResourceIssueRef,
)
from telescreen.ports.policy import (
    PolicyDecider,
    PolicyDecision,
    RiskScorer,
)


@dataclass
class IssueService:
    """Record masked findings and expose issue lifecycle operations."""

    audit: AuditLog
    events: IssueEventSink
    fingerprinter: Fingerprinter
    redactor: Redactor
    resolver: RecipientResolver
    clock: Clock
    repository: IssueRepository = field(default_factory=IssueRepository)
    decider: PolicyDecider | None = None
    scorer: RiskScorer | None = None
    owners: OwnerDirectory | None = None
    owner_routing: bool = False
    default_team: str | None = None
    findings_observer: FindingsObserver = field(default_factory=NoopFindingsObserver)
    activity: IssueActivityNotifier = field(default_factory=NoopIssueActivityNotifier)
    archive_policy: ArchivePolicy = field(default_factory=ArchivePolicy)
    inventory: InventoryObserver = field(default_factory=NoopInventoryObserver)

    async def record_scan(self, session: AsyncSession, scan: ResourceScan) -> RecordOutcome:
        """Persist one resource scan and reconcile absent classes when complete."""
        scan_values: tuple[str, ...] = (
            scan.source,
            scan.display_locator,
            scan.display_title,
            scan.ref_json or "",
        )
        if any(self.redactor.has_hits(value) for value in scan_values):
            raise TelescreenError(ErrorCode.RAW_VALUE_DETECTED, "raw sensitive value detected")
        for finding in scan.findings:
            finding_values: tuple[str, ...] = (
                finding.rule_id,
                finding.masked_snippet,
                finding.location,
            )
            if any(self.redactor.has_hits(value) for value in finding_values):
                raise TelescreenError(ErrorCode.RAW_VALUE_DETECTED, "raw sensitive value detected")
        affected: list[str] = []
        prior_visibility_pairs: tuple[tuple[str, str], ...] = tuple(
            (row.id, row.exposure_visibility)
            for row in await self.repository.resource_issues(session, scan.resource_key)
        )
        opened: int = 0
        regressed: int = 0
        inserted_total: int = 0
        for rule_class in RuleClass:
            findings: tuple[MaskedFinding, ...] = tuple(
                finding for finding in scan.findings if finding.rule_class is rule_class
            )
            if not findings:
                continue
            key: str = grouping_key(scan.source, scan.resource_key, rule_class)
            issue: IssueRow | None = await self.repository.by_grouping_key(session, key)
            created: bool = issue is None
            base: Severity = severity_for(rule_class)
            stored_visibility: Visibility = (
                Visibility(issue.exposure_visibility) if issue is not None else Visibility.UNKNOWN
            )
            visibility: Visibility = merge_visibility(
                stored_visibility, scan.exposure.visibility if scan.exposure is not None else None
            )
            current_severity: Severity = effective_severity(
                base, severity_floor(rule_class, visibility)
            )
            previous_policy: tuple[str | None, int | None] = (
                issue.policy_name if issue is not None else None,
                issue.policy_version if issue is not None else None,
            )
            placement_keys: tuple[str, ...] = scan.placement_keys
            if not placement_keys and issue is not None and issue.placement_keys_json is not None:
                placement_keys = decode_placement_keys(issue.placement_keys_json)
            age_reference: datetime = (
                scan.modified_at
                or (issue.resource_modified_at if issue is not None else None)
                or (issue.first_seen if issue is not None else scan.observed_at)
            )
            decision: PolicyDecision = await decide_policy(
                session,
                self.decider,
                scan.source,
                rule_class,
                placement_keys,
                visibility,
                age_reference,
                scan.observed_at,
                self.owner_routing,
            )
            observed_tiers: list[AssetTier] = (
                [self.scorer.asset_tier(finding.rule_id, rule_class) for finding in findings]
                if self.scorer is not None
                else []
            )
            if issue is not None and issue.asset_tier is not None:
                observed_tiers.append(AssetTier(issue.asset_tier))
            asset_tier: AssetTier | None = highest_asset_tier(observed_tiers)
            risk_score: int = score_risk(
                self.scorer,
                current_severity,
                visibility,
                asset_tier,
                issue.first_seen if issue is not None else scan.observed_at,
                scan.observed_at,
            )
            if issue is None:
                number: int = await self.repository.next_issue_number(session)
                issue = IssueRow(
                    id=new_id(),
                    number=number,
                    grouping_key=key,
                    source=scan.source,
                    resource_key=scan.resource_key,
                    display_locator=scan.display_locator,
                    resource_ref_json=scan.ref_json,
                    collector_config_id=scan.collector_config_id,
                    display_title=scan.display_title[:512],
                    rule_class=rule_class.value,
                    severity=current_severity.value,
                    status=(
                        IssueStatus.ALLOWED
                        if decision.effect.value == "allow"
                        else IssueStatus.UNRESOLVED
                    ).value,
                    priority=priority_for_severity(current_severity).value,
                    needs_review=decision.effect.value != "allow",
                    first_seen=scan.observed_at,
                    last_seen=scan.observed_at,
                    last_checked_at=scan.observed_at,
                    sweep_covered=scan.sweep_covered,
                    times_seen=0,
                    distinct_fingerprints=0,
                    regression_count=0,
                    resolved_at=None,
                    resolution=None,
                    created_at=scan.observed_at,
                    updated_at=scan.observed_at,
                    base_severity=base.value,
                    exposure_visibility=visibility.value,
                    exposure_json=exposure_json(scan.exposure)
                    if scan.exposure is not None
                    else None,
                    exposure_changed_at=scan.observed_at
                    if visibility is not Visibility.UNKNOWN
                    else None,
                    placement_keys_json=dump_json(
                        json_array(tuple(json_str(value) for value in placement_keys))
                    ),
                    resource_modified_at=scan.modified_at,
                    owner_ref=scan.owner_ref,
                    policy_name=decision.policy_name,
                    policy_version=decision.policy_version,
                    risk_score=risk_score,
                    asset_tier=asset_tier.value if asset_tier is not None else None,
                )
                session.add(issue)
                opened += int(issue.status != IssueStatus.ALLOWED.value)
                await self._history(
                    session,
                    issue.id,
                    IssueHistoryKind.CREATED,
                    "system:scanner",
                    scan.observed_at,
                )
                if issue.status != IssueStatus.ALLOWED.value:
                    await self._audit(
                        session, "issue.created", "issue", issue.id, "Issue created", 0, 0
                    )
            issue_ids_before: int = inserted_total
            prior_findings: Sequence[FindingRow] = await self.repository.findings(session, issue.id)
            existing: Sequence[FindingRow] = await self.repository.findings_for_scan(
                session, issue.id, scan.scan_job_id
            )
            existing_fingerprints: set[str] = {row.fingerprint for row in existing}
            regressed_fingerprint: bool = False
            for finding in findings:
                if finding.fingerprint in existing_fingerprints:
                    continue
                same_version_exists: bool = any(
                    row.fingerprint_key_version == finding.fingerprint_key_version
                    for row in prior_findings
                )
                if same_version_exists and all(
                    row.fingerprint != finding.fingerprint
                    or row.fingerprint_key_version != finding.fingerprint_key_version
                    for row in prior_findings
                ):
                    regressed_fingerprint = True
                row: FindingRow = FindingRow(
                    id=new_id(),
                    issue_id=issue.id,
                    scan_job_id=scan.scan_job_id,
                    source=scan.source,
                    resource_key=scan.resource_key,
                    display_locator=scan.display_locator,
                    location=finding.location,
                    rule_id=finding.rule_id,
                    rule_class=finding.rule_class.value,
                    detector=finding.detector.value,
                    confidence=finding.confidence,
                    masked_snippet=finding.masked_snippet,
                    fingerprint=finding.fingerprint,
                    fingerprint_key_id=finding.fingerprint_key_id,
                    observed_at=scan.observed_at,
                    fingerprint_key_version=finding.fingerprint_key_version,
                )
                try:
                    async with session.begin_nested():
                        session.add(row)
                        await session.flush()
                except IntegrityError:
                    continue
                existing_fingerprints.add(finding.fingerprint)
                issue.times_seen += 1
                inserted_total += 1
            if inserted_total > issue_ids_before or not created:
                previous_status: IssueStatus = IssueStatus(issue.status)
                issue.base_severity = base.value
                issue.severity = current_severity.value
                issue.exposure_visibility = visibility.value
                if scan.exposure is not None and (
                    scan.exposure.visibility is not Visibility.UNKNOWN
                    or stored_visibility is Visibility.UNKNOWN
                ):
                    issue.exposure_json = exposure_json(scan.exposure)
                issue.placement_keys_json = dump_json(
                    json_array(tuple(json_str(value) for value in placement_keys))
                )
                issue.resource_modified_at = scan.modified_at or issue.resource_modified_at
                issue.owner_ref = scan.owner_ref or issue.owner_ref
                issue.policy_name = decision.policy_name
                issue.policy_version = decision.policy_version
                issue.risk_score = risk_score
                if asset_tier is not None:
                    issue.asset_tier = asset_tier.value
                issue.last_seen = scan.observed_at
                issue.updated_at = scan.observed_at
                issue.distinct_fingerprints = await self.repository.distinct_fingerprints(
                    session, issue.id
                )
                regressed += await apply_post_insert_transitions(
                    self,
                    session,
                    issue,
                    created,
                    previous_status,
                    decision,
                    previous_policy,
                    regressed_fingerprint,
                    current_severity,
                    rule_class,
                    scan.observed_at,
                )
                if (
                    issue.merged_into_id is not None
                    and previous_status in (IssueStatus.RESOLVED, IssueStatus.PENDING_VERIFICATION)
                    and IssueStatus(issue.status) in (IssueStatus.REGRESSED, IssueStatus.UNRESOLVED)
                ):
                    await propagate_child_open(session, issue, scan.observed_at, self.events)
                if created and issue.status != IssueStatus.ALLOWED.value:
                    await self._route_owner(session, issue, decision, scan.observed_at)
            primary: IssueRow = await primary_of(session, issue)
            await recompute_group_counters(session, primary)
            if primary.status == IssueStatus.ARCHIVED.value:
                previous_visibility: Visibility = Visibility(
                    next(
                        (
                            value
                            for issue_id, value in prior_visibility_pairs
                            if issue_id == primary.id
                        ),
                        primary.exposure_visibility,
                    )
                )
                await evaluate_after_ingest(
                    session,
                    primary,
                    previous_visibility,
                    scan.observed_at,
                    self.archive_policy,
                    self.audit,
                    self.events,
                    self.activity,
                )
            if not issue.priority_manual:
                issue.priority = priority_for_severity(Severity(issue.severity)).value
            inserted_for_issue: int = inserted_total - issue_ids_before
            if inserted_for_issue > 0:
                await self.findings_observer.on_findings_recorded(
                    session,
                    FindingsRecorded(issue.id, inserted_for_issue, scan.observed_at),
                )
            if created and issue.status != IssueStatus.ALLOWED.value:
                await self.events.on_issue_event(
                    session,
                    IssueEvent(
                        IssueEventKind.ISSUE_CREATED,
                        new_id(),
                        issue.id,
                        issue.source,
                        rule_class,
                        Severity(issue.severity),
                        scan.observed_at,
                    ),
                )
            if issue.id not in affected:
                affected.append(issue.id)

        resolved: int = 0
        present_classes: set[str] = {finding.rule_class.value for finding in scan.findings}
        if scan.complete:
            for issue in await self.repository.open_for_resource(session, scan.resource_key):
                if issue.rule_class not in present_classes:
                    resolution: IssueResolution = (
                        IssueResolution.RESOURCE_DELETED
                        if scan.removed
                        else IssueResolution.VERIFIED_REMOVED
                    )
                    await resolve_auto(self, session, issue, scan.observed_at, resolution)
                    if issue.id not in affected:
                        affected.append(issue.id)
                    resolved += 1
        for issue in await self.repository.resource_issues(session, scan.resource_key):
            previous_value: str = next(
                (value for issue_id, value in prior_visibility_pairs if issue_id == issue.id),
                issue.exposure_visibility,
            )
            previous_visibility: Visibility = Visibility(previous_value)
            merged_visibility: Visibility = merge_visibility(
                previous_visibility,
                scan.exposure.visibility if scan.exposure is not None else None,
            )
            if merged_visibility is not previous_visibility:
                issue.exposure_visibility = merged_visibility.value
                issue.exposure_json = (
                    exposure_json(scan.exposure)
                    if scan.exposure is not None
                    else issue.exposure_json
                )
                issue.exposure_changed_at = scan.observed_at
                rule_class: RuleClass = RuleClass(issue.rule_class)
                issue.severity = effective_severity(
                    Severity(issue.base_severity), severity_floor(rule_class, merged_visibility)
                ).value
                issue.risk_score = score_risk(
                    self.scorer,
                    Severity(issue.severity),
                    merged_visibility,
                    AssetTier(issue.asset_tier) if issue.asset_tier is not None else None,
                    issue.first_seen,
                    scan.observed_at,
                )
                placement: tuple[str, ...] = decode_placement_keys(issue.placement_keys_json)
                prior_policy: tuple[str | None, int | None] = (
                    issue.policy_name,
                    issue.policy_version,
                )
                policy: PolicyDecision = await decide_policy(
                    session,
                    self.decider,
                    issue.source,
                    rule_class,
                    placement,
                    merged_visibility,
                    scan.modified_at or issue.resource_modified_at or issue.first_seen,
                    scan.observed_at,
                    self.owner_routing,
                )
                current_status: IssueStatus = IssueStatus(issue.status)
                if current_status in OPEN_STATUSES and policy.effect.value == "allow":
                    issue.status = IssueStatus.ALLOWED.value
                    if current_status is IssueStatus.ARCHIVED:
                        clear_archive(issue)
                    issue.needs_review = False
                    issue.resolution = (
                        IssueResolution.POLICY_CHANGED.value
                        if prior_policy != (policy.policy_name, policy.policy_version)
                        else IssueResolution.EXPOSURE_REDUCED.value
                    )
                    issue.resolved_at = scan.observed_at
                    await self._reject_requested_exceptions(session, issue.id, scan.observed_at)
                elif current_status is IssueStatus.ALLOWED and policy.effect.value == "violate":
                    issue.status = IssueStatus.UNRESOLVED.value
                    issue.resolution = None
                    issue.resolved_at = None
                    await self.events.on_issue_event(
                        session,
                        IssueEvent(
                            IssueEventKind.ISSUE_CREATED,
                            new_id(),
                            issue.id,
                            issue.source,
                            rule_class,
                            Severity(issue.severity),
                            scan.observed_at,
                        ),
                    )
                    await self._route_owner(session, issue, policy, scan.observed_at)
                issue.policy_name = policy.policy_name
                issue.policy_version = policy.policy_version
                if IssueStatus(issue.status) in RECONCILED_STATUSES:
                    await self._history(
                        session,
                        issue.id,
                        IssueHistoryKind.EXPOSURE_CHANGED,
                        "system:scanner",
                        scan.observed_at,
                        from_value=previous_visibility.value,
                        to_value=merged_visibility.value,
                    )
                if issue.id not in affected:
                    affected.append(issue.id)
            issue.last_checked_at = scan.observed_at
            issue.sweep_covered = scan.sweep_covered
            issue.updated_at = scan.observed_at
        values: tuple[ValueFingerprint, ...] = tuple(
            sorted(
                {
                    ValueFingerprint(finding.fingerprint, finding.fingerprint_key_version)
                    for finding in scan.findings
                },
                key=lambda value: (value.fingerprint, value.key_version),
            )
        )
        await self.inventory.observe(
            session,
            ResourceObservation(
                scan.source,
                scan.resource_key,
                scan.display_locator,
                scan.display_title,
                scan.collector_config_id,
                scan.scan_job_id,
                scan.observed_at,
                scan.complete,
                scan.removed,
                scan.sweep_covered,
                values,
                scan.items,
                scan.asset_kind,
            ),
        )
        return RecordOutcome(inserted_total, opened, regressed, resolved, tuple(affected))

    async def record_external(self, session: AsyncSession, scan: ExternalScan) -> RecordOutcome:
        """Validate and re-key externally supplied masked findings before recording."""
        from telescreen.issues.external_scan import record_external_scan

        return await record_external_scan(self, session, scan)

    async def add_history(self, session: AsyncSession, entry: IssueHistoryEntry) -> None:
        """Append a masked history entry in the caller transaction."""
        await self._history(
            session,
            entry.issue_id,
            entry.kind,
            entry.actor_ref,
            entry.at,
            self._safe(entry.note),
            self._safe(entry.from_value),
            self._safe(entry.to_value),
        )

    async def change_status(
        self, session: AsyncSession, issue_id: str, target: IssueStatus, actor_ref: str
    ) -> IssueRow:
        """Apply an allowed user status transition with history and audit."""
        issue: IssueRow = await self._required(session, issue_id)
        current: IssueStatus = IssueStatus(issue.status)
        ensure_user_transition(current, target)
        now: datetime = self.clock.now()
        issue.status = target.value
        issue.resolution = IssueResolution.MANUAL.value if target is IssueStatus.RESOLVED else None
        issue.resolved_at = now if target is IssueStatus.RESOLVED else None
        issue.updated_at = now
        if current is IssueStatus.IGNORED and target is IssueStatus.UNRESOLVED:
            exception: IssueExceptionRow | None = await self.repository.active_exception(
                session, issue.id
            )
            if exception is not None:
                exception.status = "revoked"
                exception.decided_by = actor_ref
                exception.decided_at = now
                await self._history(
                    session,
                    issue.id,
                    IssueHistoryKind.EXCEPTION_REVOKED,
                    actor_ref,
                    now,
                    from_value="active",
                    to_value="revoked",
                )
        if current is IssueStatus.RESOLVED and target is IssueStatus.UNRESOLVED:
            issue.resolution = None
            issue.resolved_at = None
        if current is IssueStatus.ARCHIVED:
            clear_archive(issue)
        issue.needs_review = False
        await cascade_to_children(
            session, issue, GroupChange(GroupAction.STATUS, actor_ref, now, status=target)
        )
        await self._history(
            session,
            issue.id,
            IssueHistoryKind.STATUS_CHANGED,
            actor_ref,
            now,
            from_value=current.value,
            to_value=target.value,
        )
        await self._audit(
            session,
            "issue.status_changed",
            "issue",
            issue.id,
            "Issue status changed",
            1,
            0,
            actor_ref,
            self._actor_kind(actor_ref),
        )
        await self.activity.on_activity(
            session, IssueActivity(IssueHistoryKind.STATUS_CHANGED, issue.id, actor_ref, now)
        )
        return issue

    async def assign(
        self, session: AsyncSession, issue_id: str, assignee_ref: str, actor_ref: str
    ) -> IssueRow:
        """Assign an issue to a directory member or team."""
        issue: IssueRow = await self._required(session, issue_id)
        ids: AssigneeIds = await self.resolver.assignee_of(session, assignee_ref)
        issue.assignee_member_id = ids.member_id
        issue.assignee_team_id = ids.team_id
        now: datetime = self.clock.now()
        issue.updated_at = now
        issue.needs_review = False
        await cascade_to_children(
            session,
            issue,
            GroupChange(
                GroupAction.ASSIGN,
                actor_ref,
                now,
                assignee_member_id=ids.member_id,
                assignee_team_id=ids.team_id,
            ),
        )
        await self._history(
            session,
            issue.id,
            IssueHistoryKind.ASSIGNED,
            actor_ref,
            now,
            to_value=assignee_ref,
        )
        await self._audit(
            session,
            "issue.assigned",
            "issue",
            issue.id,
            "Issue assigned",
            0,
            0,
            actor_ref,
            self._actor_kind(actor_ref),
        )
        await self.activity.on_activity(
            session,
            IssueActivity(
                IssueHistoryKind.ASSIGNED,
                issue.id,
                actor_ref,
                now,
                assignee_member_id=ids.member_id,
            ),
        )
        return issue

    async def set_priority(
        self, session: AsyncSession, issue_id: str, priority: IssuePriority, actor_ref: str
    ) -> IssueRow:
        """Set a manual priority and append history and audit."""
        issue: IssueRow = await self._required(session, issue_id)
        now: datetime = self.clock.now()
        previous: str = issue.priority
        issue.priority = priority.value
        issue.priority_manual = True
        issue.needs_review = False
        issue.updated_at = now
        await self._history(
            session,
            issue.id,
            IssueHistoryKind.PRIORITY_CHANGED,
            actor_ref,
            now,
            from_value=previous,
            to_value=priority.value,
        )
        await self._audit(
            session,
            "issue.priority_changed",
            "issue",
            issue.id,
            "Issue priority changed",
            0,
            0,
            actor_ref,
            self._actor_kind(actor_ref),
        )
        return issue

    async def mark_reviewed(self, session: AsyncSession, issue_id: str, actor_ref: str) -> IssueRow:
        """Clear the review flag and record the human triage action."""
        issue: IssueRow = await self._required(session, issue_id)
        now: datetime = self.clock.now()
        issue.needs_review = False
        issue.updated_at = now
        await self._history(session, issue.id, IssueHistoryKind.REVIEWED, actor_ref, now)
        await self._audit(
            session,
            "issue.reviewed",
            "issue",
            issue.id,
            "Issue reviewed",
            0,
            0,
            actor_ref,
            self._actor_kind(actor_ref),
        )
        return issue

    async def comment(
        self, session: AsyncSession, issue_id: str, body: str, actor_ref: str
    ) -> None:
        """Append a redacted user comment to issue history."""
        issue: IssueRow = await self._required(session, issue_id)
        note: str = self.redactor.redact(body).text[:4000]
        now: datetime = self.clock.now()
        await self._history(session, issue.id, IssueHistoryKind.COMMENTED, actor_ref, now, note)
        await self._audit(
            session,
            "issue.commented",
            "issue",
            issue.id,
            "Issue comment added",
            0,
            0,
            actor_ref,
            self._actor_kind(actor_ref),
        )

    async def resolve_unchecked(
        self, session: AsyncSession, collector_config_id: str, checked_before: datetime
    ) -> int:
        """Auto-resolve old sweep-covered issues absent from a successful sweep."""
        resolved: int = 0
        now: datetime = self.clock.now()
        for issue in await self.repository.unchecked_open(
            session, collector_config_id, ensure_utc(checked_before)
        ):
            await resolve_auto(self, session, issue, now, IssueResolution.RESOURCE_DELETED)
            resolved += 1
        return resolved

    async def get(self, session: AsyncSession, issue_id: str) -> IssueSnapshot | None:
        """Return a masked issue snapshot for other feature packages."""
        issue: IssueRow | None = await self.repository.by_id(session, issue_id)
        if issue is None:
            return None
        assignee_ref: str | None = None
        assignee_team_slug: str | None = None
        if self.owners is not None and issue.assignee_member_id is not None:
            assignee_ref = await self.owners.member_ref(session, issue.assignee_member_id)
        elif self.owners is not None and issue.assignee_team_id is not None:
            assignee_ref = await self.owners.team_ref(session, issue.assignee_team_id)
            if assignee_ref is not None and assignee_ref.startswith("team:"):
                assignee_team_slug = assignee_ref.removeprefix("team:")
        masked_excerpt: str | None = await session.scalar(
            select(FindingRow.masked_snippet)
            .where(FindingRow.issue_id == issue.id)
            .order_by(FindingRow.observed_at.desc(), FindingRow.id.desc())
            .limit(1)
        )
        return IssueSnapshot(
            id=issue.id,
            number=issue.number,
            short_id=short_id(issue.number),
            display_title=issue.display_title,
            status=IssueStatus(issue.status),
            source=issue.source,
            rule_class=RuleClass(issue.rule_class),
            severity=Severity(issue.severity),
            display_locator=issue.display_locator,
            times_seen=issue.times_seen,
            first_seen=ensure_utc(issue.first_seen),
            last_seen=ensure_utc(issue.last_seen),
            collector_config_id=issue.collector_config_id,
            ref_json=issue.resource_ref_json,
            assignee_ref=assignee_ref,
            risk_score=issue.risk_score,
            visibility=Visibility(issue.exposure_visibility),
            masked_excerpt=masked_excerpt,
            priority=IssuePriority(issue.priority),
            assignee_team_slug=assignee_team_slug,
        )

    async def open_resource_refs(
        self, session: AsyncSession, collector_config_id: str
    ) -> tuple[OpenResourceRef, ...]:
        """Return one safe resource reference per open issue resource."""
        open_statuses: tuple[str, ...] = tuple(status.value for status in RECONCILED_STATUSES)
        rows: Sequence[IssueRow] = (
            await session.scalars(
                select(IssueRow)
                .where(
                    IssueRow.collector_config_id == collector_config_id,
                    IssueRow.status.in_(open_statuses),
                )
                .order_by(IssueRow.resource_key, IssueRow.id)
            )
        ).all()
        refs: list[OpenResourceRef] = []
        seen: set[str] = set()
        for issue in rows:
            if issue.resource_key not in seen:
                refs.append(
                    OpenResourceRef(
                        issue.id, issue.resource_key, issue.resource_ref_json, issue.sweep_covered
                    )
                )
                seen.add(issue.resource_key)
        return tuple(refs)

    async def open_issues_for_resources(
        self, session: AsyncSession, resource_keys: tuple[str, ...]
    ) -> tuple[ResourceIssueRef, ...]:
        """Return open issue references for keyed resources."""
        if not resource_keys:
            return ()
        statuses: tuple[str, ...] = tuple(status.value for status in OPEN_STATUSES)
        rows: Sequence[IssueRow] = (
            await session.scalars(
                select(IssueRow)
                .where(
                    IssueRow.resource_key.in_(resource_keys),
                    IssueRow.status.in_(statuses),
                )
                .order_by(IssueRow.number)
            )
        ).all()
        return tuple(
            ResourceIssueRef(row.resource_key, row.id, short_id(row.number)) for row in rows
        )

    async def _required(self, session: AsyncSession, issue_id: str) -> IssueRow:
        """Return an issue or a safe not-found error."""
        issue: IssueRow | None = await self.repository.by_id(session, issue_id)
        if issue is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "issue not found")
        return issue

    async def _route_owner(
        self, session: AsyncSession, issue: IssueRow, decision: PolicyDecision, at: datetime
    ) -> None:
        """Assign an unassigned issue to its mapped owner or configured team."""
        await IssueOwnerRouter(self.resolver, self.owners, self.audit).route(
            session,
            issue,
            decision,
            at,
            self.default_team,
            self.owner_routing if self.decider is None else False,
        )

    async def _reject_requested_exceptions(
        self, session: AsyncSession, issue_id: str, at: datetime
    ) -> None:
        """Reject outstanding exception requests when an issue leaves open status."""
        for request in await self.repository.requested_exceptions(session, issue_id):
            request.status = "rejected"
            request.decided_by = "system"
            request.decided_at = at

    async def _history(
        self,
        session: AsyncSession,
        issue_id: str,
        kind: IssueHistoryKind,
        actor_ref: str,
        at: datetime,
        note: str | None = None,
        from_value: str | None = None,
        to_value: str | None = None,
    ) -> None:
        """Insert history with all free-form text redacted."""
        session.add(
            IssueHistoryRow(
                id=new_id(),
                issue_id=issue_id,
                kind=kind.value,
                actor_ref=actor_ref[:256],
                from_value=self._safe(from_value),
                to_value=self._safe(to_value),
                note=self._safe(note),
                at=at,
            )
        )

    def _safe(self, value: str | None) -> str | None:
        """Redact optional display text before persistence."""
        return self.redactor.redact(value).text if value is not None else None

    @staticmethod
    def _actor_kind(actor_ref: str) -> ActorKind:
        """Classify an authenticated user reference for audit records."""
        return ActorKind.MEMBER if actor_ref.startswith("member:") else ActorKind.TOKEN

    async def _audit(
        self,
        session: AsyncSession,
        action: str,
        target_kind: str,
        target_id: str,
        summary: str,
        opened: int,
        inserted: int,
        actor_ref: str = "system:issues",
        actor_kind: ActorKind = ActorKind.SYSTEM,
    ) -> None:
        """Append an audit entry containing only identifiers and counts."""
        details: JsonValue = json_object(
            (("opened", json_int(opened)), ("findings_inserted", json_int(inserted)))
        )
        await self.audit.append(
            session,
            AuditEntry(
                actor_kind,
                actor_ref,
                action,
                target_kind,
                target_id,
                summary,
                details,
            ),
        )
