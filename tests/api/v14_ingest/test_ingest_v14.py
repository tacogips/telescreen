"""Additive inventory validation and HTTP behavior for push ingestion."""

import logging
import re
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Headers
from httpx import Response as HttpxResponse
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.common import ApiContext, install_error_handlers
from telescreen.api.routers.ingest import (
    FindingsBody,
    IngestRouterDeps,
    ResourceBody,
    build_ingest_router,
)
from telescreen.audit.service import AuditLogService
from telescreen.core.json import json_object, json_str
from telescreen.core.vocabulary import ApiScope, CollectorKind, DetectorKind, TriggerMode
from telescreen.db.models import AuditRecordRow, FindingRow, IssueHistoryRow, IssueRow
from telescreen.detection import Detector, RuleDetector
from telescreen.directory import CreatedToken, TokenService
from telescreen.issues.ingest import IngestService
from telescreen.issues.scan import ResourceScannerImpl
from telescreen.masking.seal import Masker
from telescreen.ports.collectors import CollectorConfig, CustomSelector
from telescreen.ports.inventory import ResourceObservation
from tests.issues.test_issues import (
    FakeCollectorConfigReader,
    FakeScanJobStore,
    IssueHarness,
    RecordingIndexer,
    make_issue_harness,
)


@dataclass
class RecordingInventory:
    """Capture safe inventory observations made by push scans."""

    observations: list[ResourceObservation]

    async def observe(self, session: AsyncSession, observation: ResourceObservation) -> None:
        """Record the observer DTO without taking transaction ownership."""
        del session
        self.observations.append(observation)


def test_old_ingest_request_shapes_remain_valid() -> None:
    """The optional inventory fields do not change old payload validation."""
    resource: ResourceBody = TypeAdapter(ResourceBody).validate_python(
        {"locator": "source:row", "segments": [], "complete": True}
    )
    finding: FindingsBody = TypeAdapter(FindingsBody).validate_python(
        {"source": "custom:test", "locator": "source:row", "findings": []}
    )
    assert resource.item_keys is None and resource.item_count is None
    assert finding.item_fingerprints is None and finding.item_count is None


def test_removed_resource_requires_complete_empty_scan() -> None:
    """Removal cannot be inferred from content-bearing or incomplete requests."""
    with pytest.raises(ValidationError):
        TypeAdapter(ResourceBody).validate_python(
            {"locator": "safe", "removed": True, "complete": False}
        )
    with pytest.raises(ValidationError):
        TypeAdapter(ResourceBody).validate_python(
            {
                "locator": "safe",
                "removed": True,
                "segments": [{"locator": "one", "text": "content"}],
            }
        )


def test_item_fields_are_exclusive_and_bounded() -> None:
    """Invalid counts, duplicate representations, and malformed fingerprints fail closed."""
    with pytest.raises(ValidationError):
        TypeAdapter(ResourceBody).validate_python(
            {"locator": "safe", "item_keys": ["k"], "item_count": 1}
        )
    with pytest.raises(ValidationError):
        TypeAdapter(ResourceBody).validate_python({"locator": "safe", "item_keys": [""]})
    with pytest.raises(ValidationError):
        TypeAdapter(FindingsBody).validate_python(
            {"source": "custom:test", "locator": "safe", "item_fingerprints": ["bad"]}
        )
    with pytest.raises(ValidationError):
        TypeAdapter(ResourceBody).validate_python({"locator": "safe", "item_keys": ["k"] * 100001})


def test_raw_item_keys_are_hidden_from_request_repr() -> None:
    """Transient raw keys are excluded from default request representations."""
    body: ResourceBody = TypeAdapter(ResourceBody).validate_python(
        {"locator": "safe", "item_keys": ["private-item-key"]}
    )
    assert "private-item-key" not in repr(body)


@pytest.mark.asyncio
async def test_push_ingest_preserves_responses_and_tracks_removed_items(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Exercise backward-compatible response shapes and safe inventory passthrough."""
    harness: IssueHarness = await make_issue_harness()
    observer: RecordingInventory = RecordingInventory([])
    harness.service.inventory = observer
    audit: AuditLogService = AuditLogService(harness.clock, harness.redactor)
    harness.service.audit = audit
    selector: CustomSelector = CustomSelector(json_object((("mode", json_str("test")),)))
    config: CollectorConfig = CollectorConfig(
        "collector-1",
        "demo",
        CollectorKind.CUSTOM,
        TriggerMode.BOTH,
        None,
        None,
        1,
        selector,
        None,
        True,
    )
    configs: FakeCollectorConfigReader = FakeCollectorConfigReader(config)
    detector: RuleDetector = RuleDetector()

    def detector_factory(kinds: tuple[DetectorKind, ...]) -> Detector:
        """Use the real rule detector for custom push content."""
        del kinds
        return detector

    indexer: RecordingIndexer = RecordingIndexer([], [])
    scanner: ResourceScannerImpl = ResourceScannerImpl(
        harness.db.sessions,
        detector,
        detector_factory,
        Masker(harness.fingerprinter, harness.redactor),
        harness.fingerprinter,
        harness.redactor,
        harness.service,
        indexer,
        harness.clock,
        8,
        10000,
    )
    ingest: IngestService = IngestService(
        harness.db.sessions,
        scanner,
        harness.service,
        configs,
        FakeScanJobStore(),
        indexer,
        harness.clock,
    )
    try:
        async with harness.db.sessions.begin() as session:
            token_service: TokenService = TokenService(audit, harness.clock)
            api_token: CreatedToken = await token_service.create(
                session, "ingest-v14-test", frozenset((ApiScope.INGEST,)), None
            )
        ctx: ApiContext = ApiContext(harness.db.sessions, harness.clock, audit)
        app: FastAPI = FastAPI()
        install_error_handlers(app)
        app.include_router(build_ingest_router(IngestRouterDeps(ctx, ingest, configs, 8192)))
        transport: ASGITransport = ASGITransport(app=app)
        headers: Headers = Headers({"Authorization": f"Bearer {api_token.token}"})
        caplog.set_level(logging.DEBUG)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            old_scan: HttpxResponse = await client.post(
                "/api/ingest/v1/scan",
                headers=headers,
                json={"source": "custom:demo", "resources": []},
            )
            assert old_scan.status_code == 200
            assert set(old_scan.json()) == {"scan_job_id", "resources", "findings", "issue_ids"}
            old_findings: HttpxResponse = await client.post(
                "/api/ingest/v1/findings",
                headers=headers,
                json={
                    "source": "custom:demo",
                    "locator": "legacy/resource",
                    "findings": [],
                },
            )
            assert old_findings.status_code == 200
            assert set(old_findings.json()) == {"findings_inserted", "issue_ids"}

            raw_item_key: str = "item-secret-raw-key-7@example.net"
            caplog.clear()
            opened: HttpxResponse = await client.post(
                "/api/ingest/v1/scan",
                headers=headers,
                json={
                    "source": "custom:demo",
                    "resources": [
                        {
                            "locator": "repo/file",
                            "segments": [
                                {"locator": "body", "text": "contact safe-person@example.com"}
                            ],
                            "item_keys": [raw_item_key],
                        }
                    ],
                },
            )
            assert opened.status_code == 200
            issue_id: str = opened.json()["issue_ids"][0]
            assert raw_item_key not in opened.text
            assert raw_item_key not in caplog.text
            item_observation: ResourceObservation = observer.observations[-1]
            assert item_observation.items is not None
            fingerprints: tuple[str, ...] | None = item_observation.items.fingerprints
            assert fingerprints is not None and len(fingerprints) == 1
            assert re.fullmatch(r"[0-9a-f]{64}", fingerprints[0]) is not None
            assert fingerprints[0] != raw_item_key

            invalid_secret: str = "invalid-item-secret-key-" + ("x" * 1100)
            invalid: HttpxResponse = await client.post(
                "/api/ingest/v1/scan",
                headers=headers,
                json={
                    "source": "custom:demo",
                    "resources": [{"locator": "repo/file", "item_keys": [invalid_secret]}],
                },
            )
            assert invalid.status_code == 422
            assert invalid_secret not in invalid.text

            removed: HttpxResponse = await client.post(
                "/api/ingest/v1/scan",
                headers=headers,
                json={
                    "source": "custom:demo",
                    "resources": [{"locator": "repo/file", "removed": True, "complete": True}],
                },
            )
            assert removed.status_code == 200
            assert observer.observations[-1].removed

        async with harness.db.sessions() as session:
            issue: IssueRow | None = await session.get(IssueRow, issue_id)
            findings: list[FindingRow] = list((await session.scalars(select(FindingRow))).all())
            histories: list[IssueHistoryRow] = list(
                (await session.scalars(select(IssueHistoryRow))).all()
            )
            audit_rows: list[AuditRecordRow] = list(
                (await session.scalars(select(AuditRecordRow))).all()
            )
        assert issue is not None and issue.resolution == "resource_deleted"
        persisted_text: str = " ".join(
            (
                issue.id,
                issue.grouping_key,
                issue.source,
                issue.resource_key,
                issue.display_locator,
                issue.resource_ref_json or "",
                issue.collector_config_id or "",
                issue.display_title,
                issue.rule_class,
                issue.severity,
                issue.status,
                issue.assignee_member_id or "",
                issue.assignee_team_id or "",
                issue.resolution or "",
                issue.base_severity,
                issue.exposure_visibility,
                issue.exposure_json or "",
                issue.placement_keys_json or "",
                issue.owner_ref or "",
                issue.policy_name or "",
                issue.asset_tier or "",
                issue.priority,
                issue.merged_into_id or "",
                issue.archive_kind or "",
                *(
                    " ".join(
                        (
                            finding.id,
                            finding.issue_id,
                            finding.scan_job_id or "",
                            finding.source,
                            finding.resource_key,
                            finding.display_locator,
                            finding.location,
                            finding.rule_id,
                            finding.rule_class,
                            finding.detector,
                            finding.masked_snippet,
                            finding.fingerprint,
                            finding.fingerprint_key_id,
                        )
                    )
                    for finding in findings
                ),
                *(
                    " ".join(
                        (
                            history.id,
                            history.issue_id,
                            history.kind,
                            history.actor_ref,
                            history.from_value or "",
                            history.to_value or "",
                            history.note or "",
                        )
                    )
                    for history in histories
                ),
                *(
                    " ".join(
                        (
                            row.id,
                            row.actor_kind,
                            row.actor_ref,
                            row.action,
                            row.target_kind,
                            row.target_id,
                            row.summary,
                            row.details_json,
                            row.prev_hash,
                            row.hash,
                        )
                    )
                    for row in audit_rows
                ),
            )
        )
        assert raw_item_key not in persisted_text
        assert invalid_secret not in persisted_text
    finally:
        await harness.close()
