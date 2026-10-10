"""Pure placement policy evaluation over stored, versioned definitions."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.vocabulary import (
    PlacementKind,
    PolicyEffect,
    visibility_rank,
)
from telescreen.db.models import PlacementPolicyRow
from telescreen.issues.policy.loader import policy_from_json
from telescreen.issues.policy.model import PolicyDefinition
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.ports.policy import PolicyDecision, PolicySubject


@dataclass
class PlacementPolicyDecider:
    """Evaluate enabled active policies without writing or caching state."""

    fingerprinter: Fingerprinter
    owner_routing_default: bool

    async def decide(
        self, session: AsyncSession, subject: PolicySubject, now: datetime
    ) -> PolicyDecision:
        """Return the deterministic effective policy decision for a subject."""
        rows: tuple[PlacementPolicyRow, ...] = tuple(
            (
                await session.scalars(
                    select(PlacementPolicyRow)
                    .where(
                        PlacementPolicyRow.enabled.is_(True), PlacementPolicyRow.active.is_(True)
                    )
                    .order_by(PlacementPolicyRow.name)
                )
            ).all()
        )
        matching_violate: tuple[PlacementPolicyRow, PolicyDefinition] | None = None
        successful_allow: tuple[PlacementPolicyRow, PolicyDefinition] | None = None
        failed_allow: tuple[PlacementPolicyRow, PolicyDefinition] | None = None
        for row in rows:
            definition: PolicyDefinition = policy_from_json(row.compiled_json)
            if not _matches(definition, subject, self.fingerprinter):
                continue
            if definition.effect is PolicyEffect.VIOLATE:
                if matching_violate is None:
                    matching_violate = (row, definition)
            elif _conditions_hold(definition, subject, now) and successful_allow is None:
                successful_allow = (row, definition)
            elif failed_allow is None:
                failed_allow = (row, definition)
        if matching_violate is not None:
            return _decision(matching_violate[0], matching_violate[1], PolicyEffect.VIOLATE)
        if successful_allow is not None:
            return _decision(successful_allow[0], successful_allow[1], PolicyEffect.ALLOW)
        if failed_allow is not None:
            failed_row: PlacementPolicyRow = failed_allow[0]
            failed_definition: PolicyDefinition = failed_allow[1]
            return _decision(failed_row, failed_definition, PolicyEffect.VIOLATE)
        return PolicyDecision(PolicyEffect.VIOLATE, None, None, None, self.owner_routing_default)


def _decision(
    row: PlacementPolicyRow, policy: PolicyDefinition, effect: PolicyEffect
) -> PolicyDecision:
    return PolicyDecision(effect, row.name, row.version, policy.owner_team, policy.route_to_owner)


def _matches(
    policy: PolicyDefinition, subject: PolicySubject, fingerprinter: Fingerprinter
) -> bool:
    if policy.rule_classes and subject.rule_class not in policy.rule_classes:
        return False
    if policy.sources and subject.source not in tuple(source.value for source in policy.sources):
        return False
    selectors: tuple[tuple[PlacementKind, str], ...] = (
        tuple((PlacementKind.GDRIVE_DRIVE, value) for value in policy.locations.gdrive_drives)
        + tuple((PlacementKind.GDRIVE_FOLDER, value) for value in policy.locations.gdrive_folders)
        + tuple((PlacementKind.SLACK_CHANNEL, value) for value in policy.locations.slack_channels)
        + _github_selectors(policy)
    )
    if not selectors:
        return True
    return any(
        fingerprinter.placement_key(kind, value) in subject.placement_keys
        for kind, value in selectors
    )


def _github_selectors(policy: PolicyDefinition) -> tuple[tuple[PlacementKind, str], ...]:
    selectors: list[tuple[PlacementKind, str]] = []
    for selector in policy.locations.github_repos:
        owner: str
        repo: str
        owner, _, repo = selector.partition("/")
        if repo == "*":
            selectors.append((PlacementKind.GITHUB_OWNER, owner.lower()))
        else:
            selectors.append((PlacementKind.GITHUB_REPO, f"{owner}/{repo}".lower()))
    return tuple(selectors)


def _conditions_hold(policy: PolicyDefinition, subject: PolicySubject, now: datetime) -> bool:
    if policy.max_exposure is not None:
        subject_rank: int | None = visibility_rank(subject.visibility)
        limit_rank: int | None = visibility_rank(policy.max_exposure)
        if subject_rank is None or limit_rank is None or subject_rank > limit_rank:
            return False
    return policy.max_age_days is None or now - subject.age_reference <= timedelta(
        days=policy.max_age_days
    )
