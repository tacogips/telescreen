"""Session-free contracts for inventory enrichment from issue management."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ResourceIssueRef:
    """Safe link between a resource and one open issue."""

    resource_key: str
    issue_id: str
    short_id: str


class AssetIssueLookup(Protocol):
    """Read issue references without sharing an inventory transaction."""

    async def open_issues_for_resources(
        self, resource_keys: tuple[str, ...]
    ) -> tuple[ResourceIssueRef, ...]: ...


class AssetOwnerLookup(Protocol):
    """Read fallback ownership without granting access to inventory persistence."""

    async def fallback_owner(
        self, source: str, resource_key: str, display_locator: str
    ) -> str | None: ...
