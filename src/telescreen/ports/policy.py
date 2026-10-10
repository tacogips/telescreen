"""Policy and risk scoring ports shared across feature packages."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.vocabulary import (
    AssetTier,
    PolicyEffect,
    RuleClass,
    Severity,
    Visibility,
)


@dataclass(frozen=True)
class PolicySubject:
    """Safe inputs used to evaluate placement policies."""

    source: str
    rule_class: RuleClass
    placement_keys: tuple[str, ...]
    visibility: Visibility
    age_reference: datetime


@dataclass(frozen=True)
class PolicyDecision:
    """Effective policy result without raw source identifiers."""

    effect: PolicyEffect
    policy_name: str | None
    policy_version: int | None
    owner_team: str | None
    route_to_owner: bool


class PolicyDecider(Protocol):
    """Evaluate a policy subject within the caller transaction."""

    async def decide(
        self, session: AsyncSession, subject: PolicySubject, now: datetime
    ) -> PolicyDecision:
        """Return the winning placement policy decision."""
        ...


@dataclass(frozen=True)
class RiskInputs:
    """Safe ordinal inputs for calculating an issue risk score."""

    severity: Severity
    visibility: Visibility
    asset_tier: AssetTier | None
    first_seen: datetime
    now: datetime


class RiskScorer(Protocol):
    """Calculate an ordinal risk score and identify a finding's asset tier."""

    def score(self, inputs: RiskInputs) -> int:
        """Return an ordinal score for the supplied risk dimensions."""
        ...

    def asset_tier(self, rule_id: str, rule_class: RuleClass) -> AssetTier:
        """Return the asset tier used for one rule class and identifier."""
        ...
