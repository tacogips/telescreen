"""Small safe transformations used by the issue record path."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.json import JsonValue, parse_json
from telescreen.core.vocabulary import AssetTier, PolicyEffect, RuleClass, Severity, Visibility
from telescreen.db.models import FindingRow
from telescreen.ports.policy import (
    PolicyDecider,
    PolicyDecision,
    PolicySubject,
    RiskInputs,
    RiskScorer,
)

ASSET_TIER_ORDER: tuple[AssetTier, ...] = (
    AssetTier.CLOUD_ROOT,
    AssetTier.DEPLOY_TOKEN,
    AssetTier.PERSONAL_TOKEN,
    AssetTier.SECRET,
    AssetTier.PII_HIGH,
    AssetTier.PII,
)


def placement_keys(value: str | None) -> tuple[str, ...]:
    """Decode string placement references from their canonical JSON array."""
    if value is None:
        return ()
    decoded: JsonValue = parse_json(value)
    items: tuple[JsonValue, ...] | None = decoded.as_list()
    if items is None:
        return ()
    return tuple(item.str_value for item in items if item.as_str() is not None)


def highest_asset_tier(tiers: Sequence[AssetTier]) -> AssetTier | None:
    """Return the highest-ranked asset category among recorded findings."""
    return next((tier for tier in ASSET_TIER_ORDER if tier in tiers), None)


def has_version_fingerprint(findings: Sequence[FindingRow], fingerprint_key_version: int) -> bool:
    """Return whether an issue already has a fingerprint at one key version."""
    return any(row.fingerprint_key_version == fingerprint_key_version for row in findings)


async def decide_policy(
    session: AsyncSession,
    decider: PolicyDecider | None,
    source: str,
    rule_class: RuleClass,
    placement: tuple[str, ...],
    visibility: Visibility,
    age_reference: datetime,
    now: datetime,
    owner_routing: bool,
) -> PolicyDecision:
    """Evaluate a policy or return the feature-off violation decision."""
    if decider is None:
        return PolicyDecision(PolicyEffect.VIOLATE, None, None, None, owner_routing)
    return await decider.decide(
        session, PolicySubject(source, rule_class, placement, visibility, age_reference), now
    )


def score_risk(
    scorer: RiskScorer | None,
    severity: Severity,
    visibility: Visibility,
    tier: AssetTier | None,
    first_seen: datetime,
    now: datetime,
) -> int:
    """Return the configured risk score or the feature-off zero score."""
    if scorer is None:
        return 0
    return scorer.score(RiskInputs(severity, visibility, tier, first_seen, now))
