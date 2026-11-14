"""Scan, store and query evidence without retaining rejected content."""

import hashlib
from dataclasses import dataclass
from pathlib import PurePath

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import json_array, json_object, json_str
from telescreen.core.vocabulary import (
    ActorKind,
    ErrorCode,
    EvidenceVerdict,
    IssueHistoryKind,
)
from telescreen.db.models import EvidenceRow
from telescreen.detection.types import DetectionResult, Detector, ScanItem, check_results
from telescreen.evidence.image import ImageEvidenceScanner, ImageVerdict
from telescreen.evidence.query import EvidenceQueryImpl
from telescreen.evidence.store import EvidenceStore
from telescreen.evidence.strip import strip_metadata
from telescreen.evidence.types import EvidenceKind, classify
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.issues import IssueHistoryEntry, IssueRecorder


@dataclass(frozen=True)
class EvidenceService:
    """Coordinate evidence scanning and persistence at the feature boundary."""

    sessions: async_sessionmaker[AsyncSession]
    detector: Detector
    image_scanner: ImageEvidenceScanner | None
    store: EvidenceStore
    issues: IssueRecorder
    redactor: Redactor
    audit: AuditLog
    clock: Clock
    max_bytes: int

    async def upload(
        self,
        issue_id: str,
        step_run_id: str | None,
        filename: str,
        data: bytes,
        actor_ref: str,
    ) -> EvidenceRow:
        """Scan transient bytes, persist safe metadata and store accepted content only."""
        if len(data) > self.max_bytes:
            raise TelescreenError(ErrorCode.PAYLOAD_TOO_LARGE, "evidence is too large")
        kind: EvidenceKind
        content_type: str
        kind, content_type = classify(filename, data)
        display_filename: str = self._display_filename(filename)
        rejection: tuple[str, ...] = ()
        accepted_data: bytes = data
        if kind is EvidenceKind.TEXT:
            text: str = data.decode("utf-8")
            scan_item: ScanItem = ScanItem("evidence", "evidence", "evidence", text, content_type)
            results: list[DetectionResult] = await self.detector.detect_batch((scan_item,))
            check_results((scan_item,), results)
            rejection = tuple(dict.fromkeys(item.rule_class.value for item in results[0].findings))
        else:
            if self.image_scanner is None:
                raise TelescreenError(
                    ErrorCode.EVIDENCE_SCANNER_UNAVAILABLE, "image evidence scanner unavailable"
                )
            try:
                verdict: ImageVerdict = await self.image_scanner.scan(content_type, data)
                if verdict.contains_sensitive:
                    rejection = tuple(rule.value for rule in verdict.rule_classes)
                    if not rejection:
                        rejection = ("sensitive_image",)
                if not rejection:
                    accepted_data = strip_metadata(content_type, data)
            except TelescreenError as error:
                if error.code is not ErrorCode.EVIDENCE_REJECTED:
                    raise
                rejection = (error.detail,)
        if rejection:
            await self._record_rejection(
                issue_id,
                step_run_id,
                display_filename,
                content_type,
                len(data),
                actor_ref,
                rejection,
            )
            raise TelescreenError(ErrorCode.EVIDENCE_REJECTED, ",".join(rejection))
        evidence_id: str = new_id()
        self.store.put(evidence_id, accepted_data)
        row: EvidenceRow = EvidenceRow(
            id=evidence_id,
            issue_id=issue_id,
            run_id=None,
            step_run_id=step_run_id,
            display_filename=display_filename,
            content_type=content_type,
            size_bytes=len(accepted_data),
            sha256=hashlib.sha256(accepted_data).hexdigest(),
            storage_key=evidence_id,
            verdict=EvidenceVerdict.ACCEPTED.value,
            rejection_rule_classes="",
            uploaded_by=actor_ref,
            created_at=self.clock.now(),
        )
        async with self.sessions.begin() as session:
            session.add(row)
            await self.issues.add_history(
                session,
                IssueHistoryEntry(
                    issue_id,
                    IssueHistoryKind.EVIDENCE_ADDED,
                    actor_ref,
                    self.clock.now(),
                ),
            )
            await self.audit.append(
                session,
                self._audit_entry(actor_ref, "evidence.accepted", evidence_id, ()),
            )
        return row

    async def list(self, issue_id: str, step_run_id: str | None) -> tuple[EvidenceRow, ...]:
        """List accepted evidence metadata for an issue and optional step run."""
        async with self.sessions() as session:
            statement: Select[tuple[EvidenceRow]] = select(EvidenceRow).where(
                EvidenceRow.issue_id == issue_id,
                EvidenceRow.verdict == EvidenceVerdict.ACCEPTED.value,
            )
            if step_run_id is not None:
                statement = statement.where(EvidenceRow.step_run_id == step_run_id)
            rows: tuple[EvidenceRow, ...] = tuple(
                (await session.scalars(statement.order_by(EvidenceRow.created_at))).all()
            )
        return rows

    async def get(self, evidence_id: str) -> EvidenceRow:
        """Return accepted evidence metadata or a safe not-found error."""
        async with self.sessions() as session:
            row: EvidenceRow | None = await session.scalar(
                select(EvidenceRow).where(
                    EvidenceRow.id == evidence_id,
                    EvidenceRow.verdict == EvidenceVerdict.ACCEPTED.value,
                )
            )
        if row is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "evidence not found")
        return row

    async def content(self, evidence_id: str) -> bytes:
        """Return bytes only for an accepted evidence row."""
        row: EvidenceRow = await self.get(evidence_id)
        if row.storage_key is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "evidence not found")
        return self.store.get(row.storage_key)

    async def accepted_count(self, issue_id: str, step_run_id: str) -> int:
        """Count accepted evidence for an issue and workflow step run."""
        async with self.sessions() as session:
            return await EvidenceQueryImpl().accepted_count(session, issue_id, step_run_id)

    async def _record_rejection(
        self,
        issue_id: str,
        step_run_id: str | None,
        filename: str,
        content_type: str,
        size_bytes: int,
        actor_ref: str,
        rule_classes: tuple[str, ...],
    ) -> EvidenceRow:
        """Commit only safe rejection metadata and an audit entry."""
        evidence_id: str = new_id()
        row: EvidenceRow = EvidenceRow(
            id=evidence_id,
            issue_id=issue_id,
            run_id=None,
            step_run_id=step_run_id,
            display_filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            sha256=None,
            storage_key=None,
            verdict=EvidenceVerdict.REJECTED.value,
            rejection_rule_classes=",".join(rule_classes),
            uploaded_by=actor_ref,
            created_at=self.clock.now(),
        )
        async with self.sessions.begin() as session:
            session.add(row)
            await self.audit.append(
                session,
                self._audit_entry(actor_ref, "evidence.rejected", evidence_id, rule_classes),
            )
        return row

    def _audit_entry(
        self, actor_ref: str, action: str, evidence_id: str, rule_classes: tuple[str, ...]
    ) -> AuditEntry:
        """Build an audit payload from non-sensitive identifiers and rule classes."""
        actor_kind: ActorKind = (
            ActorKind.MEMBER if actor_ref.startswith("member:") else ActorKind.TOKEN
        )
        return AuditEntry(
            actor_kind,
            actor_ref,
            action,
            "evidence",
            evidence_id,
            action.replace(".", " "),
            json_object(
                (("rule_classes", json_array(tuple(json_str(value) for value in rule_classes))),)
            ),
        )

    def _display_filename(self, filename: str) -> str:
        """Persist only a basename after rule-based redaction."""
        basename: str = PurePath(filename).name[:512]
        return self.redactor.redact(basename).text
