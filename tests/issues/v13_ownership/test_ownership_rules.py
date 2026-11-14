"""Ownership rule matching, routing, suggestions, masking and route permissions."""

from dataclasses import dataclass

import pytest
from fastapi import APIRouter
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.common.auth import PermissionDependency
from telescreen.api.common.context import ApiContext
from telescreen.api.routers.ownership import OwnershipRouterDeps, build_ownership_router
from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import (
    IdentityKind,
    IdentityOrigin,
    IssueStatus,
    Role,
    SuggestionReason,
)
from telescreen.db.models import (
    IssueRow,
    MemberIdentityRow,
    MemberRow,
    OwnershipRuleRow,
    PlacementPolicyRow,
    TeamRow,
)
from telescreen.directory.owners import DirectoryOwnerResolver
from telescreen.issues.ownership import OwnershipAssigneeSuggester, OwnershipRuleService, match_rule
from telescreen.issues.routing import IssueOwnerRouter
from telescreen.masking.redact import Redactor
from telescreen.ports.directory import AssigneeIds, ResolvedRecipients
from telescreen.ports.issues import SuggestedAssignee
from telescreen.ports.policy import PolicyDecision, PolicyEffect
from tests.issues.v13_lifecycle.test_archive import NOW, RecordingAudit, _issue
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import github_token


@dataclass
class CapturingResolver:
    """Resolve test member and team refs to assignment ids."""

    teams: tuple[tuple[str, str], ...]

    async def assignee_of(self, session: AsyncSession, ref: str) -> AssigneeIds:
        """Return an id pair for configured refs."""
        del session
        if ref.startswith("team:"):
            team_id: str | None = next((item[1] for item in self.teams if item[0] == ref[5:]), None)
            return AssigneeIds(None, team_id)
        return AssigneeIds("m-owner", None)

    async def resolve(self, session: AsyncSession, ref: str) -> ResolvedRecipients:
        """Unused resolver protocol method."""
        del session, ref
        raise AssertionError("unexpected resolve")

    async def is_member_of(self, session: AsyncSession, member_id: str, ref: str) -> bool:
        """Unused resolver protocol method."""
        del session, member_id, ref
        return False


def _permission_count(route: APIRoute) -> int:
    """Count permission declarations in one route dependency tree."""

    def walk(dependencies: tuple[Dependant, ...]) -> int:
        """Count permission callables recursively."""
        total: int = 0
        for dependency in dependencies:
            if isinstance(dependency.call, PermissionDependency):
                total += 1
            total += walk(tuple(dependency.dependencies))
        return total

    return walk(tuple(route.dependant.dependencies))


async def test_match_rule_observes_order_source_and_segment_globs() -> None:
    """Ordered rules support single-segment star and recursive double-star."""
    first: OwnershipRuleRow = OwnershipRuleRow(
        id="r1",
        position=0,
        source="gitlab",
        pattern="github:org/**",
        team_id="t1",
        enabled=True,
        created_by="member:m",
        created_at=NOW,
        updated_at=NOW,
    )
    disabled: OwnershipRuleRow = OwnershipRuleRow(
        id="r2",
        position=1,
        source=None,
        pattern="github:org/*/config/**",
        team_id="t2",
        enabled=False,
        created_by="member:m",
        created_at=NOW,
        updated_at=NOW,
    )
    matched: OwnershipRuleRow = OwnershipRuleRow(
        id="r3",
        position=2,
        source="github",
        pattern="github:org/*/config/**",
        team_id="t3",
        enabled=True,
        created_by="member:m",
        created_at=NOW,
        updated_at=NOW,
    )
    assert (
        match_rule((matched, disabled, first), "github", "github:org/repo/config/app/x.yml")
        is matched
    )
    assert match_rule((matched,), "gitlab", "github:org/repo/config/app/x.yml") is None
    assert match_rule((matched,), "github", "github:org/repo/sub/config/a/x.yml") is None


@pytest.mark.asyncio
async def test_rule_test_rejects_sample_secret_without_persisting_or_echoing() -> None:
    """Patterns and test locators containing a detected sample secret are rejected."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(NOW)
    audit: RecordingAudit = RecordingAudit([])
    service: OwnershipRuleService = OwnershipRuleService(Redactor.default(), audit, clock)
    sample: str = github_token().value
    async with harness.sessions.begin() as session:
        session.add(
            TeamRow(id="t-team", slug="security", name="Security", created_at=NOW, updated_at=NOW)
        )
        with pytest.raises(TelescreenError) as pattern_error:
            await service.create(session, "github", sample, "security", True, "member:m")
        assert pattern_error.value.detail == "raw_value_detected"
        assert sample not in str(pattern_error.value)
        assert tuple((await session.scalars(select(OwnershipRuleRow))).all()) == ()
        with pytest.raises(TelescreenError) as locator_error:
            await service.test(session, "github", sample)
        assert locator_error.value.detail == "raw_value_detected"
        assert sample not in str(locator_error.value)
    await harness.close()


@pytest.mark.asyncio
async def test_reorder_requires_every_rule_id_exactly_once() -> None:
    """Incomplete reorder requests fail before changing stored positions."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(NOW)
    audit: RecordingAudit = RecordingAudit([])
    service: OwnershipRuleService = OwnershipRuleService(Redactor.default(), audit, clock)
    async with harness.sessions.begin() as session:
        session.add_all(
            (
                OwnershipRuleRow(
                    id="r1",
                    position=0,
                    source=None,
                    pattern="one/*",
                    team_id="t1",
                    enabled=True,
                    created_by="member:m",
                    created_at=NOW,
                    updated_at=NOW,
                ),
                OwnershipRuleRow(
                    id="r2",
                    position=1,
                    source=None,
                    pattern="two/*",
                    team_id="t2",
                    enabled=True,
                    created_by="member:m",
                    created_at=NOW,
                    updated_at=NOW,
                ),
            )
        )
        with pytest.raises(TelescreenError) as failure:
            await service.reorder(session, ("r1",), "member:m")
        assert failure.value.code.value == "validation_error"
        rows: tuple[OwnershipRuleRow, ...] = await service.list(session)
        assert [(row.id, row.position) for row in rows] == [("r1", 0), ("r2", 1)]
    await harness.close()


@pytest.mark.asyncio
async def test_routing_inserts_ownership_team_before_policy_team() -> None:
    """A first matching rule team is chosen before the policy team."""
    harness: SqliteHarness = await make_sqlite_harness()
    audit: RecordingAudit = RecordingAudit([])
    issue: IssueRow = _issue("ownership-route", 1, IssueStatus.UNRESOLVED)
    rule: OwnershipRuleRow = OwnershipRuleRow(
        id="r1",
        position=0,
        source="github",
        pattern="github:org/*",
        team_id="t-rule",
        enabled=True,
        created_by="member:m",
        created_at=NOW,
        updated_at=NOW,
    )
    async with harness.sessions.begin() as session:
        session.add_all(
            (
                TeamRow(
                    id="t-rule", slug="rule-team", name="Rule team", created_at=NOW, updated_at=NOW
                ),
                TeamRow(
                    id="t-policy",
                    slug="policy-team",
                    name="Policy team",
                    created_at=NOW,
                    updated_at=NOW,
                ),
                rule,
                issue,
            )
        )
        issue.source = "github"
        issue.display_locator = "github:org/repo"
        await IssueOwnerRouter(
            CapturingResolver((("rule-team", "t-rule"), ("policy-team", "t-policy"))), None, audit
        ).route(
            session,
            issue,
            PolicyDecision(PolicyEffect.VIOLATE, None, None, "policy-team", False),
            NOW,
            None,
            False,
        )
        assert issue.assignee_team_id == "t-rule"
    await harness.close()


@pytest.mark.asyncio
async def test_assignee_suggester_returns_owner_rule_then_policy_deduplicated() -> None:
    """Suggestions preserve owner, rule team and policy team order and remove duplicates."""
    harness: SqliteHarness = await make_sqlite_harness()
    issue: IssueRow = _issue("ownership-suggestions", 2)
    issue.source = "github"
    issue.display_locator = "github:org/repo"
    issue.owner_ref = f"{IdentityKind.EMAIL.value}:{'a' * 64}"
    issue.policy_name = "default"
    async with harness.sessions.begin() as session:
        session.add_all(
            (
                MemberRow(
                    id="m-owner",
                    handle="alice",
                    display_name="Alice",
                    email=None,
                    slack_user_id=None,
                    active=True,
                    created_at=NOW,
                    updated_at=NOW,
                    role=Role.ADMIN.value,
                ),
                MemberIdentityRow(
                    id="i-email",
                    member_id="m-owner",
                    kind=IdentityKind.EMAIL.value,
                    identity_key="a" * 64,
                    origin=IdentityOrigin.MANUAL.value,
                    created_at=NOW,
                ),
                TeamRow(
                    id="t-rule", slug="rule-team", name="Rule team", created_at=NOW, updated_at=NOW
                ),
                TeamRow(
                    id="t-policy",
                    slug="policy-team",
                    name="Policy team",
                    created_at=NOW,
                    updated_at=NOW,
                ),
                OwnershipRuleRow(
                    id="r1",
                    position=0,
                    source="github",
                    pattern="github:org/*",
                    team_id="t-rule",
                    enabled=True,
                    created_by="member:m",
                    created_at=NOW,
                    updated_at=NOW,
                ),
                PlacementPolicyRow(
                    id="p1",
                    name="default",
                    version=1,
                    source_format="json",
                    source_text="{}",
                    compiled_json="{}",
                    effect="violate",
                    enabled=True,
                    active=True,
                    owner_team="policy-team",
                    created_by="member:m",
                    created_at=NOW,
                ),
                issue,
            )
        )
        suggested: tuple[SuggestedAssignee, ...] = await OwnershipAssigneeSuggester(
            DirectoryOwnerResolver()
        ).suggest(session, issue.id)
    assert [(item.ref, item.reason) for item in suggested] == [
        ("member:alice", SuggestionReason.OWNER),
        ("team:rule-team", SuggestionReason.OWNERSHIP_RULE),
        ("team:policy-team", SuggestionReason.POLICY_TEAM),
    ]
    await harness.close()


@pytest.mark.asyncio
async def test_ownership_router_routes_each_have_one_permission() -> None:
    """Every ownership endpoint on the built router declares exactly one permission."""
    clock: FakeClock = FakeClock(NOW)
    audit: RecordingAudit = RecordingAudit([])
    harness: SqliteHarness = await make_sqlite_harness()
    ctx: ApiContext = ApiContext(harness.sessions, clock, audit)
    router: APIRouter = build_ownership_router(
        OwnershipRouterDeps(ctx, OwnershipRuleService(Redactor.default(), audit, clock))
    )
    routes: tuple[APIRoute, ...] = tuple(
        route for route in router.routes if isinstance(route, APIRoute)
    )
    assert len(routes) == 6
    assert all(_permission_count(route) == 1 for route in routes)
    await harness.close()
