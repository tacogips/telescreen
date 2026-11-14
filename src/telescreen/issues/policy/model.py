"""Typed placement policy definitions and version views."""

from dataclasses import dataclass
from datetime import datetime

from telescreen.core.vocabulary import (
    CollectorKind,
    PolicyEffect,
    RuleClass,
    SourceFormat,
    Visibility,
)


@dataclass(frozen=True)
class PolicyLocations:
    """Raw operator-configured selectors, kept out of finding records."""

    gdrive_drives: tuple[str, ...] = ()
    gdrive_folders: tuple[str, ...] = ()
    slack_channels: tuple[str, ...] = ()
    github_repos: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicyDefinition:
    """Validated placement policy configuration."""

    name: str
    effect: PolicyEffect
    rule_classes: tuple[RuleClass, ...]
    sources: tuple[CollectorKind, ...]
    locations: PolicyLocations
    max_exposure: Visibility | None
    max_age_days: int | None
    route_to_owner: bool
    owner_team: str | None
    rationale: str


@dataclass(frozen=True)
class PolicyVersionView:
    """Safe source and lifecycle details for one stored policy version."""

    name: str
    version: int
    effect: PolicyEffect
    enabled: bool
    active: bool
    owner_team: str | None
    source_format: SourceFormat
    source_text: str
    created_by: str
    created_at: datetime
