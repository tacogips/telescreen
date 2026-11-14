"""Custom collector push-ingestion endpoints."""

import json as json_library
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
from telescreen.core.vocabulary import CollectorKind, ErrorCode, RuleClass
from telescreen.issues.ingest import IngestScanResult, IngestService
from telescreen.ports.collectors import (
    CollectorConfig,
    CollectorConfigReader,
    CustomSelector,
    ResourceContent,
    Segment,
)
from telescreen.ports.findings import ExternalFinding, RecordOutcome


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
                    body.source,
                    None,
                    tuple(Segment(segment.locator, segment.text) for segment in resource.segments),
                    resource.complete,
                    False,
                    resource.locator,
                    resource.title or resource.locator,
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
        outcome: RecordOutcome = await deps.ingest.findings_push(
            body.source, body.locator, body.complete, findings
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
