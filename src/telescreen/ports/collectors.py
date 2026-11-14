"""Collector adapter DTOs and protocols."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.json import JsonValue
from telescreen.core.vocabulary import (
    ChannelKind,
    CollectorKind,
    CoverageState,
    DataAssetKind,
    DetectorKind,
    ErrorCode,
    IdentityKind,
    InventoryMode,
    PlacementKind,
    RepoVisibility,
    ScanJobStatus,
    ScanTrigger,
    TriggerMode,
    UnsupportedReason,
    Visibility,
)


@dataclass(frozen=True)
class InboundWebhook:
    """An inbound event envelope with sensitive transport data hidden in repr."""

    kind: CollectorKind
    method: str
    received_at: datetime
    headers: tuple[tuple[str, str], ...] = field(repr=False)
    body: bytes = field(repr=False)

    def header(self, name: str) -> str | None:
        """Return a header value using case-insensitive name matching."""
        normalized_name: str = name.casefold()
        return next(
            (
                value
                for header_name, value in self.headers
                if header_name.casefold() == normalized_name
            ),
            None,
        )


class EventCheckKind(StrEnum):
    """Validation result for an inbound collector event."""

    VALID = "valid"
    INVALID = "invalid"
    CHALLENGE = "challenge"


@dataclass(frozen=True)
class EventCheck:
    """Result of verifying an inbound event."""

    kind: EventCheckKind
    response_body: str | None = None
    config_hint: str | None = None


@dataclass(frozen=True)
class CollectorSecrets:
    """Resolved webhook secrets, excluded from diagnostic representations."""

    signing_secret: str | None = field(repr=False)
    channel_token_key: bytes | None = field(repr=False)


@dataclass(frozen=True)
class ResourceRef:
    """Opaque collector reference with transient raw locator hidden from repr."""

    kind: CollectorKind
    ref_json: str
    raw_locator: str = field(repr=False)


@dataclass(frozen=True)
class Segment:
    """One transient text segment from a monitored resource."""

    locator: str
    text: str = field(repr=False)


@dataclass(frozen=True)
class ResourceContent:
    """Transient resource content delivered directly to the scanning pipeline."""

    source: str
    collector_config_id: str | None
    segments: tuple[Segment, ...]
    complete: bool
    sweep_covered: bool
    raw_locator: str = field(repr=False)
    display_title: str = field(repr=False)
    ref: ResourceRef | None = None
    exposure: Exposure | None = None
    owner: OwnerIdentity | None = None
    placement: tuple[PlacementRef, ...] = ()
    modified_at: datetime | None = None
    coverage: Coverage | None = None
    removed: bool = False
    items: ItemReport | None = None
    asset_kind: DataAssetKind | None = None


@dataclass(frozen=True)
class ItemReport:
    """Transient raw item keys or count from a resource provider."""

    keys: tuple[str, ...] | None = field(default=None, repr=False)
    count: int | None = None


@dataclass(frozen=True)
class Exposure:
    """Transient, count-only exposure context from a provider."""

    visibility: Visibility
    external_principal_count: int = 0
    shared_drive: bool = False
    channel_kind: ChannelKind | None = None
    repo_visibility: RepoVisibility | None = None
    is_fork: bool = False


@dataclass(frozen=True)
class Coverage:
    """Safe resource scan coverage summary."""

    state: CoverageState
    reason: UnsupportedReason | None = None
    truncated_bytes: int = 0


@dataclass(frozen=True)
class OwnerIdentity:
    """Transient provider owner identity, excluded from representations."""

    kind: IdentityKind
    value: str = field(repr=False)


@dataclass(frozen=True)
class PlacementRef:
    """Transient provider container reference, excluded from representations."""

    kind: PlacementKind
    value: str = field(repr=False)


@dataclass(frozen=True)
class CollectorCursor:
    """Opaque position for incremental collector scans."""

    value: str


@dataclass(frozen=True)
class ChangePage:
    """One page of changed and removed resources."""

    changed: tuple[ResourceRef, ...]
    removed: tuple[ResourceRef, ...]
    next_cursor: CollectorCursor | None
    full_listing: bool
    has_more: bool


@dataclass(frozen=True)
class EventResources:
    """Resources associated with an event or a requested incremental poll."""

    changed: tuple[ResourceRef, ...]
    removed: tuple[ResourceRef, ...]
    poll_changes: bool = False


@dataclass(frozen=True)
class SlackSelector:
    """Slack channels selected for monitoring."""

    channel_ids: tuple[str, ...]
    include_files: bool


@dataclass(frozen=True)
class DriveSelector:
    """Drive locations and MIME types selected for monitoring."""

    drive_ids: tuple[str, ...]
    folder_ids: tuple[str, ...]
    mime_types: tuple[str, ...]


@dataclass(frozen=True)
class GitHubSelector:
    """GitHub repository and path selection."""

    owner: str
    repos: tuple[str, ...]
    branches: tuple[str, ...]
    path_globs: tuple[str, ...]


@dataclass(frozen=True)
class CustomSelector:
    """Closed JSON selector for a custom collector."""

    document: JsonValue


@dataclass(frozen=True)
class PoliceSelector:
    """Police labels and closed JSON task input selector."""

    labels: tuple[str, ...]
    input: JsonValue


type CollectorSelector = (
    SlackSelector | DriveSelector | GitHubSelector | CustomSelector | PoliceSelector
)


@dataclass(frozen=True)
class CollectorConfig:
    """Collector configuration without resolved secret material."""

    id: str
    name: str
    kind: CollectorKind
    trigger_mode: TriggerMode
    cron: str | None
    sweep_cron: str | None
    sweep_max_concurrency: int
    selector: CollectorSelector
    credential_ref: str | None
    enabled: bool
    inventory_mode: InventoryMode = InventoryMode.SENSITIVE_ONLY


@dataclass(frozen=True)
class WatchState:
    """State needed to renew a provider watch."""

    channel_id: str
    resource_id: str
    expires_at: datetime


class ResourceGoneError(Exception):
    """The upstream resource was deleted or moved to trash."""


class RateLimited(Exception):
    """An upstream service asked the collector to back off."""

    retry_after_seconds: float | None

    def __init__(self, retry_after_seconds: float | None = None) -> None:
        """Create a rate-limit signal with an optional retry delay."""
        self.retry_after_seconds = retry_after_seconds
        super().__init__("collector rate limited")


class CollectorAdapter(Protocol):
    """Per-source operations implemented by collector adapters."""

    kind: CollectorKind

    async def verify_event(self, event: InboundWebhook, secrets: CollectorSecrets) -> EventCheck:
        """Verify event authenticity and challenge handshakes."""
        ...

    async def resources_from_event(
        self, event: InboundWebhook, config: CollectorConfig
    ) -> EventResources:
        """Extract resource references from an event."""
        ...

    async def list_changed(
        self, config: CollectorConfig, cursor: CollectorCursor | None
    ) -> ChangePage:
        """List changed and removed resources since a cursor."""
        ...

    async def fetch(self, config: CollectorConfig, ref: ResourceRef) -> ResourceContent:
        """Fetch monitored content transiently for scanning."""
        ...

    async def renew_watch(
        self, config: CollectorConfig, current: WatchState | None
    ) -> WatchState | None:
        """Create or renew an upstream event watch."""
        ...

    def ref_from_json(self, ref_json: str) -> ResourceRef:
        """Rebuild an opaque reference or raise validation_error."""
        ...


class RescanKind(StrEnum):
    """Result category for a resource rescan."""

    FOUND = "found"
    CLEAN = "clean"
    ERROR = "error"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class RescanResult:
    """Safe outcome of checking an issue's source resource again."""

    kind: RescanKind
    error_code: ErrorCode | None = None
    coverage: CoverageState | None = None


class ResourceRescanner(Protocol):
    """Rescan a resource associated with an issue."""

    async def rescan(
        self, issue_id: str, detectors: tuple[DetectorKind, ...] | None = None
    ) -> RescanResult:
        """Return only a safe scan outcome and stable error code."""
        ...


class CollectorConfigReader(Protocol):
    """Read collector configuration by id or name."""

    async def get(self, session: AsyncSession, collector_config_id: str) -> CollectorConfig | None:
        """Return a collector config or None."""
        ...

    async def get_by_name(self, session: AsyncSession, name: str) -> CollectorConfig | None:
        """Return a named collector config or None."""
        ...


class ScanJobStore(Protocol):
    """Create and finish collector scan job records."""

    async def create(
        self,
        session: AsyncSession,
        collector_config_id: str | None,
        source: str,
        trigger: ScanTrigger,
        started_at: datetime,
    ) -> str:
        """Create a scan job and return its id."""
        ...

    async def finish(
        self,
        session: AsyncSession,
        scan_job_id: str,
        status: ScanJobStatus,
        resources_scanned: int,
        resources_skipped: int,
        findings_count: int,
        error_code: ErrorCode | None,
        finished_at: datetime,
    ) -> None:
        """Finish a scan job with count-only results."""
        ...
