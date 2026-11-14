"""Placement policy matching and decision precedence tests."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.json import dump_json
from telescreen.core.vocabulary import (
    PlacementKind,
    PolicyEffect,
    RuleClass,
    SourceFormat,
    Visibility,
)
from telescreen.db.models import PlacementPolicyRow
from telescreen.issues.policy.decider import PlacementPolicyDecider
from telescreen.issues.policy.loader import load_policy, policy_to_json
from telescreen.issues.policy.model import PolicyDefinition
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.ports.policy import PolicyDecision, PolicySubject
from tests.support.db import SqliteHarness, make_sqlite_harness

NOW: datetime = datetime(2026, 10, 10, tzinfo=UTC)


def add_policy(
    session: AsyncSession,
    source: str,
    version: int = 1,
) -> None:
    """Add a compiled active policy for a focused decision test."""
    policy: PolicyDefinition = load_policy(source, SourceFormat.JSON)
    session.add(
        PlacementPolicyRow(
            id=f"policy-{policy.name}",
            name=policy.name,
            version=version,
            source_format="json",
            source_text=source,
            compiled_json=dump_json(policy_to_json(policy)),
            effect=policy.effect.value,
            enabled=True,
            active=True,
            owner_team=policy.owner_team,
            created_by="member:operator",
            created_at=NOW,
        )
    )


def make_subject(
    placement_keys: tuple[str, ...] = (),
    visibility: Visibility = Visibility.PRIVATE,
    age_reference: datetime = NOW,
    rule_class: RuleClass = RuleClass.EMAIL,
) -> PolicySubject:
    """Return a common policy evaluation subject."""
    return PolicySubject("gdrive", rule_class, placement_keys, visibility, age_reference)


@pytest.mark.asyncio
async def test_no_policy_violates_with_configured_owner_routing_default() -> None:
    """Return a default violation when no placement policy applies."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        async with harness.sessions() as session:
            decision: PolicyDecision = await PlacementPolicyDecider(
                Fingerprinter(b"k" * 32, "key"), True
            ).decide(session, make_subject(), NOW)
        assert decision.effect is PolicyEffect.VIOLATE
        assert decision.policy_name is None
        assert decision.route_to_owner is True
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_exact_placement_allow_and_failed_conditions_route_to_owner() -> None:
    """Match keyed locations and fail closed on exposure or stale age."""
    harness: SqliteHarness = await make_sqlite_harness()
    fingerprinter: Fingerprinter = Fingerprinter(b"k" * 32, "key")
    key: str = fingerprinter.placement_key(PlacementKind.GDRIVE_FOLDER, "folder-1")
    try:
        async with harness.sessions.begin() as session:
            add_policy(
                session,
                '{"name":"allow","effect":"allow","match":{"locations":{"gdrive_folders":["folder-1"]}},"condition":{"max_exposure":"internal","max_age_days":365},"owner_team":"hr"}',
            )
        async with harness.sessions() as session:
            allowed: PolicyDecision = await PlacementPolicyDecider(fingerprinter, False).decide(
                session, make_subject((key,), Visibility.INTERNAL), NOW
            )
            too_public: PolicyDecision = await PlacementPolicyDecider(fingerprinter, False).decide(
                session, make_subject((key,), Visibility.PUBLIC), NOW
            )
            too_old: PolicyDecision = await PlacementPolicyDecider(fingerprinter, False).decide(
                session, make_subject((key,), Visibility.INTERNAL, NOW - timedelta(days=400)), NOW
            )
        assert allowed.effect is PolicyEffect.ALLOW
        assert too_public.effect is PolicyEffect.VIOLATE
        assert too_public.owner_team == "hr"
        assert too_old.effect is PolicyEffect.VIOLATE
        assert too_old.owner_team == "hr"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_violate_wins_and_github_owner_wildcard_matches() -> None:
    """Prefer matching violations and map owner wildcards to keyed owner selectors."""
    harness: SqliteHarness = await make_sqlite_harness()
    fingerprinter: Fingerprinter = Fingerprinter(b"k" * 32, "key")
    owner_key: str = fingerprinter.placement_key(PlacementKind.GITHUB_OWNER, "example")
    try:
        async with harness.sessions.begin() as session:
            add_policy(
                session,
                '{"name":"a-allow","effect":"allow","match":{"sources":["gdrive"]}}',
            )
            add_policy(
                session,
                '{"name":"z-violate","effect":"violate","match":{"sources":["gdrive"]}}',
            )
            add_policy(
                session,
                '{"name":"github","effect":"allow","match":{"locations":{"github_repos":["Example/*"]}}}',
            )
        async with harness.sessions() as session:
            conflict: PolicyDecision = await PlacementPolicyDecider(fingerprinter, False).decide(
                session, make_subject(), NOW
            )
            owner_wildcard: PolicyDecision = await PlacementPolicyDecider(
                fingerprinter, False
            ).decide(
                session,
                PolicySubject("github", RuleClass.EMAIL, (owner_key,), Visibility.PRIVATE, NOW),
                NOW,
            )
        assert (conflict.effect, conflict.policy_name) == (PolicyEffect.VIOLATE, "z-violate")
        assert (owner_wildcard.effect, owner_wildcard.policy_name) == (PolicyEffect.ALLOW, "github")
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_rule_class_filter_excludes_policy() -> None:
    """Do not apply a policy when its non-empty rule-class filter misses."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        async with harness.sessions.begin() as session:
            add_policy(
                session,
                '{"name":"credential-only","effect":"allow","match":{"rule_classes":["credential"]}}',
            )
        async with harness.sessions() as session:
            decision: PolicyDecision = await PlacementPolicyDecider(
                Fingerprinter(b"k" * 32, "key"), False
            ).decide(session, make_subject(rule_class=RuleClass.EMAIL), NOW)
        assert decision.effect is PolicyEffect.VIOLATE
        assert decision.policy_name is None
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_max_age_accepts_naive_age_reference_from_sqlite() -> None:
    """Naive datetimes read back from SQLite are treated as UTC, not a TypeError."""
    harness: SqliteHarness = await make_sqlite_harness()
    fingerprinter: Fingerprinter = Fingerprinter(b"k" * 32, "key")
    key: str = fingerprinter.placement_key(PlacementKind.GDRIVE_FOLDER, "folder-1")
    naive_fresh: datetime = (NOW - timedelta(days=10)).replace(tzinfo=None)
    naive_stale: datetime = (NOW - timedelta(days=400)).replace(tzinfo=None)
    try:
        async with harness.sessions.begin() as session:
            add_policy(
                session,
                '{"name":"allow","effect":"allow","match":{"locations":{"gdrive_folders":["folder-1"]}},"condition":{"max_age_days":365},"owner_team":"hr"}',
            )
        async with harness.sessions() as session:
            decider: PlacementPolicyDecider = PlacementPolicyDecider(fingerprinter, False)
            fresh: PolicyDecision = await decider.decide(
                session, make_subject((key,), Visibility.INTERNAL, naive_fresh), NOW
            )
            stale: PolicyDecision = await decider.decide(
                session, make_subject((key,), Visibility.INTERNAL, naive_stale), NOW
            )
        assert fresh.effect is PolicyEffect.ALLOW
        assert stale.effect is PolicyEffect.VIOLATE
    finally:
        await harness.close()
