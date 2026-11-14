"""Regression coverage for issue-record privacy, routing, and reconciliation."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import json_null
from telescreen.core.vocabulary import (
    AssetTier,
    CoverageState,
    DetectorKind,
    EngineTaskKind,
    ErrorCode,
    ExceptionDuration,
    ExceptionStatus,
    IdentityKind,
    IdentityOrigin,
    IssueEventKind,
    IssueHistoryKind,
    IssueStatus,
    PlacementKind,
    PolicyEffect,
    RuleClass,
    UnsupportedReason,
)
from telescreen.db.models import (
    IssueExceptionRow,
    IssueHistoryRow,
    IssueRow,
    MemberIdentityRow,
    MemberRow,
)
from telescreen.detection import Detector, RuleDetector
from telescreen.directory.owners import DirectoryOwnerResolver
from telescreen.issues.policy_apply import IssuePolicyMaintenance, PolicyReevaluateHandler
from telescreen.issues.routing import IssueOwnerRouter
from telescreen.issues.scan import ResourceScannerImpl
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.seal import Masker
from telescreen.ports.collectors import (
    Coverage,
    OwnerIdentity,
    PlacementRef,
    ResourceContent,
    Segment,
)
from telescreen.ports.directory import (
    AssigneeIds,
    OwnerDirectory,
    RecipientResolver,
    ResolvedRecipients,
)
from telescreen.ports.findings import MaskedFinding, ResourceScan
from telescreen.ports.policy import PolicyDecision, RiskInputs, RiskScorer
from telescreen.ports.workflow import ClaimedTask, TaskResult, TaskResultKind
from tests.issues.test_issues import (
    IssueHarness,
    RecordingIndexer,
    make_issue_harness,
    masked_finding,
    resource_scan,
)
from tests.issues.v11_record.test_recording import StaticDecider


@dataclass(frozen=True)
class FakeOwners(OwnerDirectory):
    """Resolve only the keyed owner configured by a test."""

    owner_ref: str | None
    member_id: str | None

    async def member_ref(self, session: AsyncSession, member_id: str) -> str | None:
        del session
        return f"member:{member_id}" if member_id == self.member_id else None

    async def team_ref(self, session: AsyncSession, team_id: str) -> str | None:
        del session
        return f"team:{team_id}"

    async def team_refs_of(self, session: AsyncSession, member_id: str) -> tuple[str, ...]:
        del session, member_id
        return ()

    async def member_for_owner(self, session: AsyncSession, owner_ref: str) -> str | None:
        del session
        if owner_ref != self.owner_ref or self.member_id is None:
            return None
        return f"member:{self.member_id}"

    async def team_lead(self, session: AsyncSession, team_slug: str) -> str | None:
        del session, team_slug
        return None


@dataclass(frozen=True)
class FakeResolver(RecipientResolver):
    """Resolve configured assignee refs and optionally reject selected refs."""

    assignees: tuple[tuple[str, AssigneeIds], ...]
    rejected_refs: tuple[str, ...] = ()

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients:
        del session, ref
        return ResolvedRecipients((), (), (), ())

    async def is_member_of(self, session: AsyncSession, member_id: str, ref: str) -> bool:
        del session, member_id, ref
        return False

    async def assignee_of(self, session: AsyncSession, ref: str) -> AssigneeIds:
        del session
        if ref in self.rejected_refs:
            raise TelescreenError(ErrorCode.RECIPIENT_UNKNOWN, "unknown test recipient")
        return next(
            (assignee for candidate, assignee in self.assignees if candidate == ref),
            AssigneeIds(None, None),
        )


@dataclass
class FakeScorer(RiskScorer):
    """Return deterministic score and asset tier for record-path assertions."""

    score_value: int
    tier: AssetTier

    def score(self, inputs: RiskInputs) -> int:
        del inputs
        return self.score_value

    def asset_tier(self, rule_id: str, rule_class: RuleClass) -> AssetTier:
        del rule_id, rule_class
        return self.tier


def issue_decision(
    effect: PolicyEffect,
    policy_version: int,
    owner_team: str | None = None,
    route_to_owner: bool = False,
) -> PolicyDecision:
    """Build the stable placement-policy decision used by these tests."""
    return PolicyDecision(effect, "records", policy_version, owner_team, route_to_owner)


async def record_issue(harness: IssueHarness, marker: str) -> str:
    """Create one unresolved issue and return its opaque database id."""
    finding: MaskedFinding = masked_finding(harness, RuleClass.EMAIL, marker)
    async with harness.db.sessions.begin() as session:
        await harness.service.record_scan(session, resource_scan(harness, (finding,), marker))
        issue: IssueRow = list((await session.scalars(select(IssueRow))).all())[0]
        return issue.id


@pytest.mark.asyncio
async def test_allowed_to_violate_records_event_history_and_owner_routing() -> None:
    """A newly violating allowed issue is reopened, notified, and routed to its owner."""
    harness: IssueHarness = await make_issue_harness()
    try:
        owner: OwnerIdentity = OwnerIdentity(IdentityKind.EMAIL, "owner@example.invalid")
        owner_ref: str = harness.fingerprinter.owner_ref(owner)
        harness.service.owners = FakeOwners(owner_ref, "member-7")
        harness.service.resolver = FakeResolver(
            (("member:member-7", AssigneeIds("member-7", None)),)
        )
        harness.service.decider = StaticDecider(issue_decision(PolicyEffect.ALLOW, 1))
        first: MaskedFinding = masked_finding(harness, RuleClass.EMAIL, "allowed-then-violate-one")
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session, resource_scan(harness, (first,), "allowed-first")
            )
        assert harness.events.events == []
        harness.service.decider = StaticDecider(
            issue_decision(PolicyEffect.VIOLATE, 2, route_to_owner=True)
        )
        second: MaskedFinding = masked_finding(harness, RuleClass.EMAIL, "allowed-then-violate-two")
        violating_scan: ResourceScan = replace(
            resource_scan(harness, (second,), "allowed-second"), owner_ref=owner_ref
        )
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(session, violating_scan)
        async with harness.db.sessions() as session:
            issue: IssueRow = list((await session.scalars(select(IssueRow))).all())[0]
            history: list[IssueHistoryRow] = list(
                (await session.scalars(select(IssueHistoryRow))).all()
            )
        assert issue.status == IssueStatus.UNRESOLVED.value
        assert issue.assignee_member_id == "member-7"
        assert len(harness.events.events) == 1
        assert harness.events.events[0].kind is IssueEventKind.ISSUE_CREATED
        assert any(
            row.kind == IssueHistoryKind.POLICY_REEVALUATED.value
            and row.from_value == IssueStatus.ALLOWED.value
            and row.to_value == IssueStatus.UNRESOLVED.value
            for row in history
        )
        assert any(
            row.kind == IssueHistoryKind.ASSIGNED.value
            and row.actor_ref == "system:owner-routing"
            and row.to_value == "member:member-7"
            for row in history
        )
    finally:
        await harness.close()


@pytest.mark.parametrize(
    ("scenario", "expected_member", "expected_team"),
    (
        ("owner", "member-7", None),
        ("owner_team", None, "ops"),
        ("default_team", None, "default"),
        ("none", None, None),
        ("resolver_error", None, "ops"),
    ),
)
@pytest.mark.asyncio
async def test_owner_router_fallbacks(
    scenario: str, expected_member: str | None, expected_team: str | None
) -> None:
    """Routing tries owner, policy team, default team, and then leaves unassigned."""
    harness: IssueHarness = await make_issue_harness()
    try:
        issue_id: str = await record_issue(harness, f"route-{scenario}")
        owner_ref: str = "email:keyed-owner"
        issue_owners: FakeOwners = FakeOwners(owner_ref, "member-7")
        refs: tuple[tuple[str, AssigneeIds], ...] = (
            ("member:member-7", AssigneeIds("member-7", None)),
            ("team:ops", AssigneeIds(None, "ops-id")),
            ("team:default", AssigneeIds(None, "default-id")),
        )
        rejected: tuple[str, ...] = ("member:member-7",) if scenario == "resolver_error" else ()
        resolver: FakeResolver = FakeResolver(refs, rejected)
        harness.service.resolver = resolver
        router: IssueOwnerRouter = IssueOwnerRouter(resolver, issue_owners, harness.audit)
        harness.service.default_team = "default" if scenario == "default_team" else None
        decision: PolicyDecision = issue_decision(
            PolicyEffect.VIOLATE,
            1,
            owner_team="ops" if scenario in ("owner_team", "resolver_error") else None,
            route_to_owner=scenario in ("owner", "resolver_error"),
        )
        async with harness.db.sessions.begin() as session:
            issue: IssueRow | None = await session.get(IssueRow, issue_id)
            assert issue is not None
            issue.owner_ref = owner_ref
            if scenario == "none":
                issue_owners = FakeOwners(None, None)
                router = IssueOwnerRouter(resolver, issue_owners, harness.audit)
            await router.route(
                session,
                issue,
                decision,
                harness.clock.now(),
                harness.service.default_team,
                False,
            )
        async with harness.db.sessions() as session:
            issue = await session.get(IssueRow, issue_id)
            history: list[IssueHistoryRow] = list(
                (await session.scalars(select(IssueHistoryRow))).all()
            )
        assert issue is not None
        assert issue.assignee_member_id == expected_member
        expected_team_id: str | None = (
            "ops-id"
            if expected_team == "ops"
            else "default-id"
            if expected_team == "default"
            else expected_team
        )
        assert issue.assignee_team_id == expected_team_id
        routed: bool = expected_member is not None or expected_team_id is not None
        assert any(row.kind == IssueHistoryKind.ASSIGNED.value for row in history) is routed
        if routed:
            assigned: IssueHistoryRow = next(
                row for row in history if row.kind == IssueHistoryKind.ASSIGNED.value
            )
            assert assigned.actor_ref == "system:owner-routing"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_owner_router_assigns_owner_through_real_directory_resolver() -> None:
    """The member ref returned by the real owner directory is assigned without remapping."""
    harness: IssueHarness = await make_issue_harness()
    try:
        issue_id: str = await record_issue(harness, "route-real-owner")
        owner_ref: str = f"{IdentityKind.EMAIL.value}:{'b' * 64}"
        async with harness.db.sessions.begin() as session:
            session.add(
                MemberRow(
                    id="member-route-owner",
                    handle="route-owner",
                    display_name="Route Owner",
                    email=None,
                    slack_user_id=None,
                    active=True,
                    created_at=harness.clock.now(),
                    updated_at=harness.clock.now(),
                )
            )
            session.add(
                MemberIdentityRow(
                    id="identity-route-owner",
                    member_id="member-route-owner",
                    kind=IdentityKind.EMAIL.value,
                    identity_key="b" * 64,
                    origin=IdentityOrigin.MEMBER_EMAIL.value,
                    created_at=harness.clock.now(),
                )
            )
        router: IssueOwnerRouter = IssueOwnerRouter(
            harness.service.resolver, DirectoryOwnerResolver(), harness.audit
        )
        async with harness.db.sessions.begin() as session:
            issue: IssueRow | None = await session.get(IssueRow, issue_id)
            assert issue is not None
            issue.owner_ref = owner_ref
            await router.route(
                session,
                issue,
                issue_decision(PolicyEffect.VIOLATE, 1, route_to_owner=True),
                harness.clock.now(),
                None,
                False,
            )
        async with harness.db.sessions() as session:
            issue = await session.get(IssueRow, issue_id)
        assert issue is not None
        assert issue.assignee_member_id == "member-route-owner"
        assert issue.assignee_team_id is None
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_existing_assignee_is_not_overridden_by_owner_router() -> None:
    """A preexisting assignment wins over all owner and team fallbacks."""
    harness: IssueHarness = await make_issue_harness()
    try:
        issue_id: str = await record_issue(harness, "route-existing")
        resolver: FakeResolver = FakeResolver((("member:member-7", AssigneeIds("member-7", None)),))
        router: IssueOwnerRouter = IssueOwnerRouter(
            resolver, FakeOwners("email:keyed-owner", "member-7"), harness.audit
        )
        async with harness.db.sessions.begin() as session:
            issue: IssueRow | None = await session.get(IssueRow, issue_id)
            assert issue is not None
            issue.assignee_team_id = "existing-team"
            await router.route(
                session,
                issue,
                issue_decision(PolicyEffect.VIOLATE, 1, "ops", True),
                harness.clock.now(),
                "default",
                True,
            )
        async with harness.db.sessions() as session:
            issue = await session.get(IssueRow, issue_id)
            history: list[IssueHistoryRow] = list(
                (await session.scalars(select(IssueHistoryRow))).all()
            )
        assert issue is not None
        assert issue.assignee_team_id == "existing-team"
        assert issue.assignee_member_id is None
        assert not any(row.kind == IssueHistoryKind.ASSIGNED.value for row in history)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_record_path_allow_uses_policy_changed_when_version_differs() -> None:
    """A newly activated policy version is distinguished from exposure reduction."""
    harness: IssueHarness = await make_issue_harness()
    try:
        harness.service.decider = StaticDecider(issue_decision(PolicyEffect.VIOLATE, 1))
        first: MaskedFinding = masked_finding(harness, RuleClass.EMAIL, "policy-version-one")
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(session, resource_scan(harness, (first,), "p1"))
        harness.service.decider = StaticDecider(issue_decision(PolicyEffect.ALLOW, 2))
        second: MaskedFinding = masked_finding(harness, RuleClass.EMAIL, "policy-version-two")
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(session, resource_scan(harness, (second,), "p2"))
        async with harness.db.sessions() as session:
            issue: IssueRow = list((await session.scalars(select(IssueRow))).all())[0]
        assert issue.status == IssueStatus.ALLOWED.value
        assert issue.policy_version == 2
        assert issue.resolution == "policy_changed"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_scanner_builds_keyed_metadata_and_marks_partial_coverage_incomplete() -> None:
    """ResourceScan carries keyed owner/placement refs and safe scan metadata only."""
    harness: IssueHarness = await make_issue_harness()
    try:
        detector: Detector = RuleDetector()

        def detector_factory(kinds: tuple[DetectorKind, ...]) -> Detector:
            del kinds
            return detector

        scanner: ResourceScannerImpl = ResourceScannerImpl(
            harness.db.sessions,
            detector,
            detector_factory,
            Masker(harness.fingerprinter, harness.redactor),
            harness.fingerprinter,
            harness.redactor,
            harness.service,
            RecordingIndexer([], []),
            harness.clock,
            4,
            10000,
        )
        raw_owner: str = "private-owner-canary@example.invalid"
        raw_placement: str = "private-folder-name-canary"
        modified: datetime = harness.clock.now() - timedelta(days=2)
        owner: OwnerIdentity = OwnerIdentity(IdentityKind.EMAIL, raw_owner)
        content: ResourceContent = ResourceContent(
            "custom:test",
            "collector-1",
            (Segment("segment:1", "ordinary text"),),
            True,
            False,
            "resource:private-canary",
            "Safe title",
            owner=owner,
            placement=(PlacementRef(PlacementKind.GDRIVE_FOLDER, raw_placement),),
            modified_at=modified,
            coverage=Coverage(CoverageState.PARTIAL, UnsupportedReason.IMAGE),
            removed=True,
        )
        result: ResourceScan = scanner._resource_scan(content, "scan-safe", (), True, modified)
        assert result.owner_ref == harness.fingerprinter.owner_ref(owner)
        assert result.placement_keys == (
            harness.fingerprinter.placement_key(PlacementKind.GDRIVE_FOLDER, raw_placement),
        )
        assert result.modified_at == modified
        assert result.removed is True
        assert result.complete is False
        assert raw_owner not in repr(result) and raw_placement not in repr(result)
        string_fields: tuple[str, ...] = (
            result.source,
            result.resource_key,
            result.display_locator,
            result.display_title,
            result.owner_ref or "",
            *result.placement_keys,
        )
        assert all(raw_owner not in value and raw_placement not in value for value in string_fields)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_distinct_fingerprints_uses_highest_key_version_only() -> None:
    """Key rotation changes occurrence counts without changing resource identity."""
    harness: IssueHarness = await make_issue_harness()
    try:
        resource_key: str = harness.fingerprinter.resource_key("custom:test", "resource:1")
        old_findings: tuple[MaskedFinding, ...] = (
            masked_finding(harness, RuleClass.EMAIL, "old-key-one"),
            masked_finding(harness, RuleClass.EMAIL, "old-key-two"),
        )
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session, resource_scan(harness, old_findings, "old-key")
            )
        rotated: Fingerprinter = Fingerprinter(b"y" * 32, "rotated", 2, b"x" * 32)
        harness.fingerprinter = rotated
        harness.service.fingerprinter = rotated
        rotated_finding: MaskedFinding = replace(
            masked_finding(harness, RuleClass.EMAIL, "new-key-only"), fingerprint_key_version=2
        )
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session, resource_scan(harness, (rotated_finding,), "new-key")
            )
        async with harness.db.sessions() as session:
            issue: IssueRow = list((await session.scalars(select(IssueRow))).all())[0]
        assert issue.resource_key == resource_key
        assert issue.distinct_fingerprints == 1
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_new_key_version_alone_does_not_expire_until_regressed() -> None:
    """A fingerprint from a new key version cannot establish same-version regression."""
    harness: IssueHarness = await make_issue_harness()
    try:
        initial: MaskedFinding = masked_finding(
            harness, RuleClass.EMAIL, "version-one-before-ignore"
        )
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session, resource_scan(harness, (initial,), "version-one")
            )
            issue: IssueRow = list((await session.scalars(select(IssueRow))).all())[0]
            issue.status = IssueStatus.IGNORED.value
            session.add(
                IssueExceptionRow(
                    id="exception-new-version",
                    issue_id=issue.id,
                    reason="approved temporarily",
                    duration=ExceptionDuration.UNTIL_REGRESSED.value,
                    status=ExceptionStatus.ACTIVE.value,
                    requested_by="member:owner",
                    approver_ref="member:lead",
                    decided_by="member:lead",
                    decided_at=harness.clock.now(),
                    expires_at=None,
                    created_at=harness.clock.now(),
                )
            )
        rotated: Fingerprinter = Fingerprinter(b"y" * 32, "rotated", 2, b"x" * 32)
        harness.fingerprinter = rotated
        harness.service.fingerprinter = rotated
        new_finding: MaskedFinding = replace(
            masked_finding(harness, RuleClass.EMAIL, "version-two-only"),
            fingerprint_key_version=2,
        )
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session, resource_scan(harness, (new_finding,), "version-two")
            )
        async with harness.db.sessions() as session:
            issue = list((await session.scalars(select(IssueRow))).all())[0]
            exception: IssueExceptionRow | None = await session.get(
                IssueExceptionRow, "exception-new-version"
            )
            history: list[IssueHistoryRow] = list(
                (await session.scalars(select(IssueHistoryRow))).all()
            )
        assert issue.status == IssueStatus.IGNORED.value
        assert exception is not None and exception.status == ExceptionStatus.ACTIVE.value
        assert not any(row.kind == IssueHistoryKind.REGRESSED.value for row in history)
    finally:
        await harness.close()


@pytest.mark.parametrize("path", ("auto_resolve", "allow"))
@pytest.mark.asyncio
async def test_requested_exception_is_rejected_when_issue_leaves_open(path: str) -> None:
    """Requested exceptions are rejected when a record scan resolves or allows issue."""
    harness: IssueHarness = await make_issue_harness()
    try:
        issue_id: str = await record_issue(harness, f"requested-{path}")
        async with harness.db.sessions.begin() as session:
            session.add(
                IssueExceptionRow(
                    id=f"requested-{path}",
                    issue_id=issue_id,
                    reason="pending approval",
                    duration=ExceptionDuration.DAYS_30.value,
                    status=ExceptionStatus.REQUESTED.value,
                    requested_by="member:owner",
                    approver_ref=None,
                    decided_by=None,
                    decided_at=None,
                    expires_at=None,
                    created_at=harness.clock.now(),
                )
            )
        if path == "allow":
            harness.service.decider = StaticDecider(issue_decision(PolicyEffect.ALLOW, 1))
            finding: MaskedFinding = masked_finding(
                harness, RuleClass.EMAIL, f"requested-{path}-next"
            )
            async with harness.db.sessions.begin() as session:
                await harness.service.record_scan(
                    session, resource_scan(harness, (finding,), f"requested-{path}-scan")
                )
        else:
            async with harness.db.sessions.begin() as session:
                await harness.service.record_scan(
                    session, resource_scan(harness, (), f"requested-{path}-clean")
                )
        async with harness.db.sessions() as session:
            issue: IssueRow | None = await session.get(IssueRow, issue_id)
            exception: IssueExceptionRow | None = await session.get(
                IssueExceptionRow, f"requested-{path}"
            )
        assert issue is not None and exception is not None
        assert issue.status == (
            IssueStatus.ALLOWED.value if path == "allow" else IssueStatus.RESOLVED.value
        )
        assert exception.status == ExceptionStatus.REJECTED.value
        assert exception.decided_by == "system"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_issue_policy_maintenance_is_hourly_and_refreshes_risk() -> None:
    """Maintenance refreshes risk once, skips 30 minutes, then refreshes after an hour."""
    harness: IssueHarness = await make_issue_harness()
    try:
        scorer: FakeScorer = FakeScorer(17, AssetTier.CLOUD_ROOT)
        harness.service.scorer = scorer
        issue_id: str = await record_issue(harness, "maintenance-risk")
        maintenance: IssuePolicyMaintenance = IssuePolicyMaintenance(harness.service)
        scorer.score_value = 19
        async with harness.db.sessions.begin() as session:
            first_count: int = await maintenance.tick(session, harness.clock.now())
        assert first_count == 1
        scorer.score_value = 23
        harness.clock.advance(timedelta(minutes=30))
        async with harness.db.sessions.begin() as session:
            skipped_count: int = await maintenance.tick(session, harness.clock.now())
            issue: IssueRow | None = await session.get(IssueRow, issue_id)
            assert issue is not None and issue.risk_score == 19
        assert skipped_count == 0
        harness.clock.advance(timedelta(minutes=31))
        async with harness.db.sessions.begin() as session:
            later_count: int = await maintenance.tick(session, harness.clock.now())
            issue = await session.get(IssueRow, issue_id)
        assert later_count == 1
        assert issue is not None and issue.risk_score == 23
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_issue_policy_maintenance_reopens_allowed_issue_with_full_transition() -> None:
    """An age-policy violation records, notifies, routes, and updates the allowed issue."""
    harness: IssueHarness = await make_issue_harness()
    try:
        scorer: FakeScorer = FakeScorer(73, AssetTier.CLOUD_ROOT)
        harness.service.scorer = scorer
        harness.service.decider = StaticDecider(issue_decision(PolicyEffect.ALLOW, 1))
        issue_id: str = await record_issue(harness, "maintenance-allowed")
        harness.service.resolver = FakeResolver((("team:ops", AssigneeIds(None, "team-ops")),))
        maintenance: IssuePolicyMaintenance = IssuePolicyMaintenance(harness.service)
        scorer.score_value = 83
        harness.service.decider = StaticDecider(
            issue_decision(PolicyEffect.VIOLATE, 2, owner_team="ops")
        )
        harness.clock.advance(timedelta(hours=1))
        now: datetime = harness.clock.now()
        async with harness.db.sessions.begin() as session:
            changed_count: int = await maintenance.tick(session, now)
        async with harness.db.sessions() as session:
            issue: IssueRow | None = await session.get(IssueRow, issue_id)
            history: list[IssueHistoryRow] = list(
                (
                    await session.scalars(
                        select(IssueHistoryRow).where(IssueHistoryRow.issue_id == issue_id)
                    )
                ).all()
            )
        reevaluations: list[IssueHistoryRow] = [
            row for row in history if row.kind == IssueHistoryKind.POLICY_REEVALUATED.value
        ]
        assignments: list[IssueHistoryRow] = [
            row for row in history if row.kind == IssueHistoryKind.ASSIGNED.value
        ]
        assert changed_count == 1
        assert issue is not None
        assert issue.status == IssueStatus.UNRESOLVED.value
        assert issue.resolution is None and issue.resolved_at is None
        assert issue.policy_name == "records" and issue.policy_version == 2
        assert ensure_utc(issue.updated_at) == now
        assert issue.risk_score == 83
        assert issue.assignee_team_id == "team-ops"
        assert len(reevaluations) == 1
        assert reevaluations[0].actor_ref == "system:policy"
        assert reevaluations[0].from_value == IssueStatus.ALLOWED.value
        assert reevaluations[0].to_value == IssueStatus.UNRESOLVED.value
        assert len(assignments) == 1
        assert assignments[0].actor_ref == "system:owner-routing"
        assert assignments[0].to_value == "team:ops"
        assert len(harness.events.events) == 1
        assert harness.events.events[0].kind is IssueEventKind.ISSUE_CREATED
        assert issue.resolution not in ("verified_removed", "resource_deleted")

        harness.clock.advance(timedelta(minutes=30))
        async with harness.db.sessions.begin() as session:
            skipped_count: int = await maintenance.tick(session, harness.clock.now())
        assert skipped_count == 0
        assert len(harness.events.events) == 1
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_record_path_stores_score_and_keeps_highest_asset_tier() -> None:
    """The score refreshes while the highest tier observed remains stored."""
    harness: IssueHarness = await make_issue_harness()
    try:
        scorer: FakeScorer = FakeScorer(73, AssetTier.CLOUD_ROOT)
        harness.service.scorer = scorer
        first: MaskedFinding = masked_finding(harness, RuleClass.EMAIL, "tier-cloud-root")
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(session, resource_scan(harness, (first,), "tier-1"))
        scorer.score_value = 41
        scorer.tier = AssetTier.PII
        second: MaskedFinding = masked_finding(harness, RuleClass.EMAIL, "tier-pii")
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(session, resource_scan(harness, (second,), "tier-2"))
        async with harness.db.sessions() as session:
            issue: IssueRow = list((await session.scalars(select(IssueRow))).all())[0]
        assert issue.risk_score == 41
        assert issue.asset_tier == AssetTier.CLOUD_ROOT.value
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_policy_reevaluate_emits_event_without_remediation_resolution() -> None:
    """An allowed-to-violate policy task emits one event and never resolves issues."""
    harness: IssueHarness = await make_issue_harness()
    try:
        harness.service.decider = StaticDecider(issue_decision(PolicyEffect.ALLOW, 1))
        finding: MaskedFinding = masked_finding(harness, RuleClass.EMAIL, "handler-policy-allowed")
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session, resource_scan(harness, (finding,), "handler-allowed")
            )
        indexer: RecordingIndexer = RecordingIndexer([], [])
        handler: PolicyReevaluateHandler = PolicyReevaluateHandler(
            harness.db.sessions, harness.service, indexer, harness.clock
        )
        harness.service.decider = StaticDecider(issue_decision(PolicyEffect.VIOLATE, 2))
        task: ClaimedTask = ClaimedTask(
            "task-policy-integrity",
            EngineTaskKind.POLICY_REEVALUATE,
            "policy",
            json_null(),
            1,
            5,
            "policy-test",
        )
        event_count: int = len(harness.events.events)
        result: TaskResult = await handler.handle(task)
        async with harness.db.sessions() as session:
            issues: list[IssueRow] = list((await session.scalars(select(IssueRow))).all())
        assert result.kind is TaskResultKind.DONE
        assert len(harness.events.events) == event_count + 1
        assert harness.events.events[-1].kind is IssueEventKind.ISSUE_CREATED
        assert all(issue.resolution != "verified_removed" for issue in issues)
    finally:
        await harness.close()
