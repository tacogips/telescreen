"""Evidence scanning, storage, API and metadata stripping tests."""

import json
import stat
import struct
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Headers
from httpx import Response as HttpxResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.common import ApiContext, install_error_handlers
from telescreen.api.common.auth import hash_token
from telescreen.api.routers.evidence import EvidenceRouterDeps, build_evidence_router
from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import (
    ApiScope,
    ErrorCode,
    RuleClass,
)
from telescreen.db.models import ApiTokenRow, EvidenceRow, IssueHistoryRow, MemberRow
from telescreen.detection import RuleDetector
from telescreen.detection.harness import LlmJsonRequest
from telescreen.evidence import (
    EvidenceService,
    ImageEvidenceScanner,
    ImageVerdict,
    LlmImageEvidenceScanner,
    LocalFileEvidenceStore,
    classify,
    strip_metadata,
)
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry
from telescreen.ports.findings import ExternalScan, RecordOutcome, ResourceScan
from telescreen.ports.issues import IssueHistoryEntry
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import Sample, assert_no_leaks, github_token

NOW: datetime = datetime(2026, 10, 9, tzinfo=UTC)


@dataclass
class RecordingAudit:
    """Capture safe audit entries in tests."""

    entries: list[AuditEntry]

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Record an audit item without writing to the database."""
        del session
        self.entries.append(entry)
        return len(self.entries)


@dataclass
class RecordingIssues:
    """Capture issue history calls in tests."""

    entries: list[IssueHistoryEntry]

    async def record_scan(self, session: AsyncSession, scan: ResourceScan) -> RecordOutcome:
        """Reject unrelated recording operations in this evidence-only fake."""
        del session, scan
        raise AssertionError("unexpected record_scan call")

    async def record_external(self, session: AsyncSession, scan: ExternalScan) -> RecordOutcome:
        """Reject unrelated recording operations in this evidence-only fake."""
        del session, scan
        raise AssertionError("unexpected record_external call")

    async def add_history(self, session: AsyncSession, entry: IssueHistoryEntry) -> None:
        """Persist issue history in the caller's transaction."""
        session.add(
            IssueHistoryRow(
                id=f"history-{len(self.entries)}",
                issue_id=entry.issue_id,
                kind=entry.kind.value,
                actor_ref=entry.actor_ref,
                from_value=entry.from_value,
                to_value=entry.to_value,
                note=entry.note,
                at=entry.at,
            )
        )
        self.entries.append(entry)

    async def resolve_unchecked(
        self, session: AsyncSession, collector_config_id: str, checked_before: datetime
    ) -> int:
        """Reject unrelated removal reconciliation in this evidence-only fake."""
        del session, collector_config_id, checked_before
        raise AssertionError("unexpected resolve_unchecked call")


@dataclass(frozen=True)
class FixedImageScanner:
    """Return a configured image verdict without inspecting bytes."""

    verdict: ImageVerdict

    async def scan(self, content_type: str, data: bytes) -> ImageVerdict:
        """Return the fixture verdict."""
        del content_type, data
        return self.verdict


@dataclass
class FakeLlmClient:
    """Return configured JSON responses to the image scanner."""

    responses: list[str]
    requests: list[LlmJsonRequest]

    async def complete_json(self, request: LlmJsonRequest) -> str:
        """Capture a request and return its next response."""
        self.requests.append(request)
        return self.responses.pop(0)


def _png_chunk(name: bytes, payload: bytes) -> bytes:
    """Build one PNG chunk with its CRC."""
    body: bytes = name + payload
    return (
        len(payload).to_bytes(4, "big") + body + (zlib.crc32(body) & 0xFFFFFFFF).to_bytes(4, "big")
    )


def _png(metadata: bytes) -> bytes:
    """Build a valid small PNG carrying a text metadata chunk."""
    header: bytes = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"tEXt", metadata)
        + _png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        + _png_chunk(b"IEND", b"")
    )


def _jpeg(metadata: bytes) -> bytes:
    """Build a minimal JPEG with EXIF, comment and scan segments."""
    app0: bytes = b"\xff\xe0\x00\x04JF"
    app1: bytes = b"\xff\xe1" + (len(metadata) + 2).to_bytes(2, "big") + metadata
    comment: bytes = b"\xff\xfe\x00\x04xy"
    scan_header: bytes = b"\xff\xda\x00\x02"
    return b"\xff\xd8" + app0 + app1 + comment + scan_header + b"pixels\xff\xd9"


def _webp(metadata: bytes = b"gps!") -> bytes:
    """Build a WebP VP8X container with EXIF and XMP chunks."""
    vp8x: bytes = bytes((0x0C,)) + bytes(9)
    exif_padding: bytes = b"\x00" if len(metadata) % 2 else b""
    xmp_padding: bytes = b"\x00" if len(metadata) % 2 else b""
    chunks: bytes = (
        b"VP8X"
        + len(vp8x).to_bytes(4, "little")
        + vp8x
        + b"EXIF"
        + len(metadata).to_bytes(4, "little")
        + metadata
        + exif_padding
        + b"XMP "
        + len(metadata).to_bytes(4, "little")
        + metadata
        + xmp_padding
    )
    payload: bytes = b"WEBP" + chunks
    return b"RIFF" + len(payload).to_bytes(4, "little") + payload


async def _service(
    harness: SqliteHarness,
    root: Path,
    scanner: ImageEvidenceScanner | None,
    audit: RecordingAudit,
    issues: RecordingIssues,
) -> EvidenceService:
    """Build a service on the SQLite harness."""
    return EvidenceService(
        harness.sessions,
        RuleDetector(),
        scanner,
        LocalFileEvidenceStore(root),
        issues,
        Redactor.default(),
        audit,
        FakeClock(NOW),
        1024 * 1024,
    )


@pytest.mark.asyncio
async def test_text_rejection_and_clean_acceptance_never_store_raw_content(tmp_path: Path) -> None:
    """Reject sensitive text and store clean text with history and audit."""
    harness: SqliteHarness = await make_sqlite_harness()
    audit: RecordingAudit = RecordingAudit([])
    issues: RecordingIssues = RecordingIssues([])
    store_root: Path = tmp_path / "evidence"
    service: EvidenceService = await _service(harness, store_root, None, audit, issues)
    sample: Sample = github_token()
    with pytest.raises(TelescreenError) as rejected:
        await service.upload(
            "issue-1", "step-1", "credentials.txt", sample.value.encode(), "member:1"
        )
    assert rejected.value.code is ErrorCode.EVIDENCE_REJECTED
    assert rejected.value.detail == RuleClass.TOKEN.value
    assert not store_root.exists()
    async with harness.sessions() as session:
        rows: list[EvidenceRow] = list((await session.scalars(select(EvidenceRow))).all())
    assert len(rows) == 1
    assert rows[0].verdict == "rejected"
    assert rows[0].sha256 is None and rows[0].storage_key is None
    assert_no_leaks(
        json.dumps([rows[0].display_filename, rows[0].rejection_rule_classes]), "evidence row"
    )
    clean: EvidenceRow = await service.upload(
        "issue-1", "step-1", "notes.txt", b"Routine remediation notes", "member:1"
    )
    assert clean.verdict == "accepted"
    assert await service.content(clean.id) == b"Routine remediation notes"
    assert clean.storage_key is not None
    assert stat.S_IMODE((store_root / clean.storage_key).stat().st_mode) == 0o600
    assert len(issues.entries) == 1
    assert audit.entries[-1].action == "evidence.accepted"
    assert await service.accepted_count("issue-1", "step-1") == 1
    assert await service.accepted_count("other-issue", "step-1") == 0
    assert await service.accepted_count("issue-1", "other-step") == 0
    await harness.close()


@pytest.mark.asyncio
async def test_image_metadata_is_stripped_and_sensitive_verdict_is_rejected(tmp_path: Path) -> None:
    """Strip PNG/JPEG metadata and reject images the scanner flags."""
    harness: SqliteHarness = await make_sqlite_harness()
    audit: RecordingAudit = RecordingAudit([])
    issues: RecordingIssues = RecordingIssues([])
    service: EvidenceService = await _service(
        harness,
        tmp_path / "evidence",
        FixedImageScanner(ImageVerdict(False, ())),
        audit,
        issues,
    )
    sample: Sample = github_token()
    for filename, payload in (
        ("photo.png", _png(sample.value.encode())),
        ("photo.jpg", _jpeg(sample.value.encode())),
    ):
        row: EvidenceRow = await service.upload("issue-2", "step-2", filename, payload, "member:1")
        stored: bytes = await service.content(row.id)
        assert_no_leaks(stored.decode("latin-1"), "stored image bytes")
    sensitive: EvidenceService = await _service(
        harness,
        tmp_path / "rejected",
        FixedImageScanner(ImageVerdict(True, (RuleClass.TOKEN,))),
        audit,
        issues,
    )
    with pytest.raises(TelescreenError) as rejected:
        await sensitive.upload("issue-2", "step-2", "private.png", _png(b"x"), "member:1")
    assert rejected.value.detail == RuleClass.TOKEN.value
    assert not (tmp_path / "rejected").exists()
    await harness.close()


@pytest.mark.asyncio
async def test_truncated_png_upload_records_only_safe_rejection_metadata(tmp_path: Path) -> None:
    """Reject malformed PNG uploads without storing their bytes."""
    harness: SqliteHarness = await make_sqlite_harness()
    service: EvidenceService = await _service(
        harness,
        tmp_path / "evidence",
        FixedImageScanner(ImageVerdict(False, ())),
        RecordingAudit([]),
        RecordingIssues([]),
    )
    with pytest.raises(TelescreenError) as rejected:
        await service.upload("issue", "step", "broken.png", _png(b"metadata")[:-5], "member:1")
    assert rejected.value.code is ErrorCode.EVIDENCE_REJECTED
    assert rejected.value.detail == "malformed_image"
    async with harness.sessions() as session:
        rows: list[EvidenceRow] = list((await session.scalars(select(EvidenceRow))).all())
    assert len(rows) == 1
    assert rows[0].verdict == "rejected"
    assert rows[0].rejection_rule_classes == "malformed_image"
    assert rows[0].sha256 is None and rows[0].storage_key is None
    assert not (tmp_path / "evidence").exists()
    await harness.close()


def test_classification_rejects_mismatched_unknown_and_invalid_utf8() -> None:
    """Reject unsupported extensions, mismatched signatures and invalid UTF-8."""
    cases: tuple[tuple[str, bytes], ...] = (
        ("image.png", b"\xff\xd8\xffjpeg"),
        ("payload.exe", b"MZ"),
        ("notes.txt", b"\xff"),
    )
    for filename, content in cases:
        with pytest.raises(TelescreenError) as rejected:
            classify(filename, content)
        assert rejected.value.code is ErrorCode.UNSUPPORTED_MEDIA_TYPE


def test_strippers_handle_webp_flags_and_reject_truncated_images() -> None:
    """Validate image container structure, metadata removal and WebP size repair."""
    stripped_webp: bytes = strip_metadata("image/webp", _webp())
    assert int.from_bytes(stripped_webp[4:8], "little") == len(stripped_webp) - 8
    assert stripped_webp[12:16] == b"VP8X"
    assert stripped_webp[20] & 0x0C == 0
    assert b"EXIF" not in stripped_webp and b"XMP " not in stripped_webp
    stripped_png: bytes = strip_metadata("image/png", _png(b"metadata"))
    assert b"tEXt" not in stripped_png
    metadata_types: frozenset[bytes] = frozenset({b"tEXt", b"iTXt", b"zTXt", b"eXIf", b"tIME"})
    offset: int = 8
    saw_iend: bool = False
    while offset < len(stripped_png):
        length: int = int.from_bytes(stripped_png[offset : offset + 4], "big")
        chunk_type: bytes = stripped_png[offset + 4 : offset + 8]
        chunk_end: int = offset + 12 + length
        assert chunk_end <= len(stripped_png)
        chunk_data: bytes = stripped_png[offset + 8 : offset + 8 + length]
        stored_crc: int = int.from_bytes(stripped_png[offset + 8 + length : chunk_end], "big")
        assert stored_crc == zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        assert chunk_type not in metadata_types
        offset = chunk_end
        if chunk_type == b"IEND":
            saw_iend = True
            assert length == 0 and offset == len(stripped_png)
            break
    assert saw_iend
    stripped_jpeg: bytes = strip_metadata("image/jpeg", _jpeg(b"exif"))
    assert b"\xff\xe1" not in stripped_jpeg and b"\xff\xfe" not in stripped_jpeg
    assert b"\xff\xe0" in stripped_jpeg and stripped_jpeg.endswith(b"\xff\xd9")
    with pytest.raises(TelescreenError) as malformed:
        strip_metadata("image/png", _png(b"meta")[:-5])
    assert malformed.value.code is ErrorCode.EVIDENCE_REJECTED
    assert malformed.value.detail == "malformed_image"


@pytest.mark.asyncio
async def test_missing_scanner_and_invalid_llm_output_fail_closed(tmp_path: Path) -> None:
    """Reject images without a scanner and fail closed on free-text model output."""
    harness: SqliteHarness = await make_sqlite_harness()
    service: EvidenceService = await _service(
        harness, tmp_path / "evidence", None, RecordingAudit([]), RecordingIssues([])
    )
    with pytest.raises(TelescreenError) as unavailable:
        await service.upload("issue", None, "photo.png", _png(b""), "member:1")
    assert unavailable.value.code is ErrorCode.EVIDENCE_SCANNER_UNAVAILABLE
    async with harness.sessions() as session:
        assert (await session.scalars(select(EvidenceRow))).all() == []
    client: FakeLlmClient = FakeLlmClient(["This image is clean." for _ in range(3)], [])
    scanner: LlmImageEvidenceScanner = LlmImageEvidenceScanner(client, "model")
    with pytest.raises(TelescreenError) as invalid:
        await scanner.scan("image/png", _png(b""))
    assert invalid.value.code is ErrorCode.EVIDENCE_REJECTED
    assert invalid.value.detail == "scanner_invalid_output"
    assert len(client.requests) == 3
    assert all(request.system.find("Never transcribe") >= 0 for request in client.requests)
    assert all(len(request.images) == 1 for request in client.requests)
    upload_client: FakeLlmClient = FakeLlmClient(["This image is clean." for _ in range(3)], [])
    upload_scanner: LlmImageEvidenceScanner = LlmImageEvidenceScanner(upload_client, "model")
    invalid_service: EvidenceService = await _service(
        harness,
        tmp_path / "invalid-output",
        upload_scanner,
        RecordingAudit([]),
        RecordingIssues([]),
    )
    with pytest.raises(TelescreenError) as invalid_upload:
        await invalid_service.upload("issue", None, "photo.png", _png(b""), "member:1")
    assert invalid_upload.value.code is ErrorCode.EVIDENCE_REJECTED
    assert invalid_upload.value.detail == "scanner_invalid_output"
    assert not (tmp_path / "invalid-output").exists()
    async with harness.sessions() as session:
        rejected_rows: list[EvidenceRow] = list((await session.scalars(select(EvidenceRow))).all())
    assert len(rejected_rows) == 1
    assert rejected_rows[0].rejection_rule_classes == "scanner_invalid_output"
    assert rejected_rows[0].sha256 is None and rejected_rows[0].storage_key is None
    await harness.close()


@pytest.mark.asyncio
async def test_api_rejection_and_over_size_are_safe(tmp_path: Path) -> None:
    """Return rule classes only on rejection and enforce the bounded body limit."""
    harness: SqliteHarness = await make_sqlite_harness()
    audit: RecordingAudit = RecordingAudit([])
    issues: RecordingIssues = RecordingIssues([])
    service: EvidenceService = await _service(
        harness,
        tmp_path / "evidence",
        FixedImageScanner(ImageVerdict(False, ())),
        audit,
        issues,
    )
    clock: FakeClock = FakeClock(NOW)
    ctx: ApiContext = ApiContext(harness.sessions, clock, audit)
    async with harness.sessions.begin() as session:
        session.add(
            MemberRow(
                id="member-1",
                handle="member-1",
                display_name="Member One",
                email=None,
                slack_user_id=None,
                active=True,
                created_at=NOW,
                updated_at=NOW,
                role="responder",
            )
        )
        session.add(
            ApiTokenRow(
                id="token-1",
                label="test",
                token_hash=hash_token("test-token"),
                scopes=ApiScope.READ.value + "," + ApiScope.WRITE.value,
                member_id="member-1",
                created_at=NOW,
                revoked_at=None,
            )
        )
    app: FastAPI = FastAPI()
    install_error_handlers(app)
    app.include_router(build_evidence_router(EvidenceRouterDeps(ctx, service, 256)))
    transport: ASGITransport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers: Headers = Headers({"Authorization": "Bearer test-token"})
        too_large: HttpxResponse = await client.put(
            "/api/evidence?issue_id=issue&filename=note.txt",
            headers=headers,
            content=b"0" * 300,
        )
        assert too_large.status_code == 413
        mismatched: HttpxResponse = await client.put(
            "/api/evidence?issue_id=issue&filename=private.png",
            headers=headers,
            content=b"\xff\xd8\xffjpeg-fixture",
        )
        assert mismatched.status_code == 415
        assert "private.png" not in mismatched.text and "jpeg-fixture" not in mismatched.text
        unsupported: HttpxResponse = await client.put(
            "/api/evidence?issue_id=issue&filename=private.exe",
            headers=headers,
            content=b"unsupported-fixture",
        )
        assert unsupported.status_code == 415
        assert "private.exe" not in unsupported.text
        assert "unsupported-fixture" not in unsupported.text
        sample: Sample = github_token()
        # The service limit is deliberately larger than the router limit in this test.
        rejected: HttpxResponse = await client.put(
            "/api/evidence?issue_id=issue&filename=token.txt",
            headers=headers,
            content=sample.value.encode(),
        )
        assert rejected.status_code == 422
        assert rejected.json()["error"]["rule_classes"] == [RuleClass.TOKEN.value]
        assert "rule_classes" in rejected.json()["error"]
        assert_no_leaks(rejected.text, "rejected API response")
        accepted: HttpxResponse = await client.put(
            "/api/evidence?issue_id=issue&filename=notes.txt",
            headers=headers,
            content=b"Clean remediation notes",
        )
        assert accepted.status_code == 201
        evidence_id: str = accepted.json()["id"]
        listed: HttpxResponse = await client.get("/api/evidence?issue_id=issue", headers=headers)
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"]] == [evidence_id]
        metadata: HttpxResponse = await client.get(f"/api/evidence/{evidence_id}", headers=headers)
        assert metadata.status_code == 200
        assert metadata.json()["verdict"] == "accepted"
        content: HttpxResponse = await client.get(
            f"/api/evidence/{evidence_id}/content", headers=headers
        )
        assert content.status_code == 200
        assert content.content == b"Clean remediation notes"
    await harness.close()


@pytest.mark.asyncio
async def test_webp_upload_strips_secret_metadata_and_repairs_container(tmp_path: Path) -> None:
    """Strip WebP EXIF/XMP secrets and retain a valid metadata-free container."""
    harness: SqliteHarness = await make_sqlite_harness()
    sample: Sample = github_token()
    store_root: Path = tmp_path / "evidence"
    service: EvidenceService = await _service(
        harness,
        store_root,
        FixedImageScanner(ImageVerdict(False, ())),
        RecordingAudit([]),
        RecordingIssues([]),
    )
    row: EvidenceRow = await service.upload(
        "issue", "step", "photo.webp", _webp(sample.value.encode()), "member:1"
    )
    stored: bytes = await service.content(row.id)
    assert_no_leaks(stored.decode("latin-1"), "stored WebP bytes")
    assert int.from_bytes(stored[4:8], "little") == len(stored) - 8
    assert stored[12:16] == b"VP8X"
    assert stored[20] & 0x0C == 0
    assert b"EXIF" not in stored and b"XMP " not in stored
    await harness.close()


@pytest.mark.asyncio
async def test_api_missing_image_scanner_returns_503_without_storage(tmp_path: Path) -> None:
    """Fail closed over HTTP when no image scanner is configured."""
    harness: SqliteHarness = await make_sqlite_harness()
    audit: RecordingAudit = RecordingAudit([])
    service: EvidenceService = await _service(
        harness, tmp_path / "evidence", None, audit, RecordingIssues([])
    )
    ctx: ApiContext = ApiContext(harness.sessions, FakeClock(NOW), audit)
    async with harness.sessions.begin() as session:
        session.add(
            MemberRow(
                id="member-1",
                handle="member-1",
                display_name="Member One",
                email=None,
                slack_user_id=None,
                active=True,
                created_at=NOW,
                updated_at=NOW,
                role="responder",
            )
        )
        session.add(
            ApiTokenRow(
                id="token-1",
                label="test",
                token_hash=hash_token("test-token"),
                scopes=ApiScope.READ.value + "," + ApiScope.WRITE.value,
                member_id="member-1",
                created_at=NOW,
                revoked_at=None,
            )
        )
    app: FastAPI = FastAPI()
    install_error_handlers(app)
    app.include_router(build_evidence_router(EvidenceRouterDeps(ctx, service, 256)))
    transport: ASGITransport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response: HttpxResponse = await client.put(
            "/api/evidence?issue_id=issue&filename=photo.png",
            headers=Headers({"Authorization": "Bearer test-token"}),
            content=_png(b"metadata"),
        )
        assert response.status_code == 503
        assert_no_leaks(response.text, "scanner-unavailable API response")
    async with harness.sessions() as session:
        assert (await session.scalars(select(EvidenceRow))).all() == []
    assert not (tmp_path / "evidence").exists()
    await harness.close()
