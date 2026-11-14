"""Custom collector push-ingestion endpoints."""

import json as json_library
import re
from dataclasses import dataclass, field
from typing import Annotated

import pydantic
from fastapi import APIRouter, Depends, Request, Response
from pydantic import TypeAdapter, ValidationError

from telescreen.api.common.auth import Principal, require_permission
from telescreen.api.common.body import read_limited_body
from telescreen.api.common.context import ApiContext
from telescreen.core.errors import TelescreenError
from telescreen.core.json import dump_json
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import CollectorKind, DataAssetKind, ErrorCode, RuleClass
from telescreen.issues.ingest import IngestScanResult, IngestService
from telescreen.ports.collectors import (
    CollectorConfig,
    CollectorConfigReader,
    CustomSelector,
    ItemReport,
    ResourceContent,
    Segment,
)
from telescreen.ports.findings import ExternalFinding, LocatorInventory, RecordOutcome


@pydantic.dataclasses.dataclass
class SegmentBody:
    """One transient segment from a push scan."""

    locator: str
    text: str = field(repr=False)


@pydantic.dataclasses.dataclass
class ResourceBody:
    """One transient resource from a push scan."""

    locator: str = field(repr=False)
    segments: list[SegmentBody] = field(default_factory=list, repr=False)
    title: str | None = field(default=None, repr=False)
    complete: bool = True
    removed: bool = False
    item_keys: list[str] | None = field(default=None, repr=False)
    item_count: int | None = None
    kind: DataAssetKind | None = None

    def __post_init__(self) -> None:
        """Reject invalid inventory values without exposing raw keys."""
        if self.removed and (self.segments or not self.complete):
            raise ValueError("invalid inventory payload")
        if self.item_keys is not None and self.item_count is not None:
            raise ValueError("invalid inventory payload")
        if self.item_keys is not None and (
            len(self.item_keys) > 100000
            or any(not key or len(key) > 1024 for key in self.item_keys)
        ):
            raise ValueError("invalid inventory payload")
        if self.item_count is not None and not 0 <= self.item_count <= 9007199254740991:
            raise ValueError("invalid inventory payload")


@pydantic.dataclasses.dataclass
class IngestScanBody:
    """Bounded request body for content scanning."""

    source: str
    resources: list[ResourceBody] = field(default_factory=list, repr=False)


@pydantic.dataclasses.dataclass
class ExternalFindingBody:
    """One already-masked finding supplied by a custom detector."""

    locator: str = field(repr=False)
    rule_id: str
    rule_class: RuleClass
    masked_snippet: str = field(repr=False)
    location: str = field(repr=False)
    confidence: float
    external_fingerprint: str = field(repr=False)


@pydantic.dataclasses.dataclass
class FindingsBody:
    """Request body for already-masked custom findings."""

    source: str
    locator: str = field(repr=False)
    complete: bool = True
    findings: list[ExternalFindingBody] = field(default_factory=list, repr=False)
    item_fingerprints: list[str] | None = None
    item_count: int | None = None
    kind: DataAssetKind | None = None

    def __post_init__(self) -> None:
        """Reject invalid inventory values without echoing them."""
        if self.item_fingerprints is not None and self.item_count is not None:
            raise ValueError("invalid inventory payload")
        if self.item_fingerprints is not None and (
            len(self.item_fingerprints) > 10000
            or any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in self.item_fingerprints)
        ):
            raise ValueError("invalid inventory payload")
        if self.item_count is not None and not 0 <= self.item_count <= 9007199254740991:
            raise ValueError("invalid inventory payload")


@dataclass(frozen=True)
class IngestRouterDeps:
    """Dependencies required by ingestion routes."""

    ctx: ApiContext
    ingest: IngestService
    configs: CollectorConfigReader
    max_scan_bytes: int = 10_485_760


def build_ingest_router(deps: IngestRouterDeps) -> APIRouter:
    """Build authenticated custom ingestion endpoints."""
    router: APIRouter = APIRouter()

    @router.post("/api/ingest/v1/scan")
    async def scan_push(
        request: Request,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.INGEST_PUSH))
        ],
    ) -> Response:
        """Read a bounded payload and scan its content transiently."""
        del principal
        body_bytes: bytes = await read_limited_body(request, deps.max_scan_bytes)
        try:
            body: IngestScanBody = TypeAdapter(IngestScanBody).validate_json(body_bytes)
        except ValidationError:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid ingestion payload") from None
        resources: list[ResourceContent] = []
        for resource in body.resources:
            resources.append(
                ResourceContent(
                    source=body.source,
                    collector_config_id=None,
                    segments=tuple(
                        Segment(segment.locator, segment.text) for segment in resource.segments
                    ),
                    complete=resource.complete,
                    sweep_covered=False,
                    raw_locator=resource.locator,
                    display_title=resource.title or resource.locator,
                    removed=resource.removed,
                    items=(
                        ItemReport(tuple(resource.item_keys), None)
                        if resource.item_keys is not None
                        else ItemReport(None, resource.item_count)
                        if resource.item_count is not None
                        else None
                    ),
                    asset_kind=resource.kind,
                )
            )
        result: IngestScanResult = await deps.ingest.scan_push(body.source, resources)
        return Response(
            content=json_library.dumps(
                {
                    "scan_job_id": result.scan_job_id,
                    "resources": result.resources,
                    "findings": result.findings,
                    "issue_ids": result.issue_ids,
                }
            ),
            media_type="application/json",
        )

    @router.post("/api/ingest/v1/findings")
    async def findings_push(
        request: Request,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.INGEST_PUSH))
        ],
    ) -> Response:
        """Re-scan external masked findings and store only re-keyed fingerprints."""
        del principal
        body_bytes: bytes = await read_limited_body(request, deps.max_scan_bytes)
        try:
            body: FindingsBody = TypeAdapter(FindingsBody).validate_json(body_bytes)
        except ValidationError:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid ingestion payload") from None
        findings: tuple[ExternalFinding, ...] = tuple(
            ExternalFinding(
                item.locator,
                item.rule_id,
                item.rule_class,
                item.masked_snippet,
                item.location,
                item.confidence,
                item.external_fingerprint,
            )
            for item in body.findings
        )
        inventory: LocatorInventory | None = (
            LocatorInventory(
                body.locator,
                tuple(body.item_fingerprints) if body.item_fingerprints is not None else None,
                body.item_count,
                body.kind,
            )
            if body.item_fingerprints is not None
            or body.item_count is not None
            or body.kind is not None
            else None
        )
        outcome: RecordOutcome = await deps.ingest.findings_push(
            body.source, body.locator, body.complete, findings, inventory
        )
        return Response(
            content=json_library.dumps(
                {"findings_inserted": outcome.findings_inserted, "issue_ids": outcome.issue_ids}
            ),
            media_type="application/json",
        )

    @router.get("/api/ingest/v1/sources/{name}/config")
    async def source_config(
        name: str,
        principal: Annotated[
            Principal, Depends(require_permission(deps.ctx, Permission.INGEST_PUSH))
        ],
    ) -> Response:
        """Return an enabled custom collector's selector as native JSON."""
        del principal
        async with deps.ctx.sessions() as session:
            config: CollectorConfig | None = await deps.configs.get_by_name(session, name)
        if config is None or config.kind is not CollectorKind.CUSTOM or not config.enabled:
            raise TelescreenError(ErrorCode.UNKNOWN_SOURCE, "unknown ingestion source")
        if not isinstance(config.selector, CustomSelector):
            raise TelescreenError(ErrorCode.UNKNOWN_SOURCE, "unknown ingestion source")
        return Response(content=dump_json(config.selector.document), media_type="application/json")

    return router
