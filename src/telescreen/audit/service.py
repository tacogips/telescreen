"""Audit append service."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.audit.chain import (
    GENESIS_HASH,
    MAIN_AUDIT_MODELS,
    AuditChainModels,
    AuditHeadModel,
    AuditHeadValue,
    compute_hash,
)
from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import dump_json
from telescreen.core.vocabulary import ErrorCode
from telescreen.db.models import AuditReceiptRow
from telescreen.masking.audit import redact_audit_entry
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.audit_commands import AuditCommand, AuditReceipt, encode_audit_command


@dataclass(frozen=True)
class AuditLogService(AuditLog):
    """Append masked entries to a serialized, hash-chained audit log."""

    clock: Clock
    redactor: Redactor
    models: AuditChainModels = MAIN_AUDIT_MODELS

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Append one masked event within the caller's transaction."""
        head: AuditHeadValue = await self._head(session)
        return await self._append(session, entry, head, ensure_utc(self.clock.now()))

    async def append_command(self, session: AsyncSession, command: AuditCommand) -> AuditReceipt:
        """Serialize receipt checking with all writers of the global chain head."""
        digest: str = sha256(encode_audit_command(command).encode("utf-8")).hexdigest()
        head: AuditHeadValue = await self._head(session)
        receipt: AuditReceiptRow | None = await session.get(AuditReceiptRow, command.event_id)
        if receipt is not None:
            if receipt.payload_sha256 != digest:
                raise TelescreenError(ErrorCode.CONFLICT, "audit event identity conflict")
            return AuditReceipt.DUPLICATE
        await self._append(session, command.entry, head, ensure_utc(command.occurred_at))
        session.add(
            AuditReceiptRow(
                id=command.event_id,
                payload_sha256=digest,
                applied_at=ensure_utc(self.clock.now()),
            )
        )
        await session.flush()
        return AuditReceipt.APPLIED

    async def _head(self, session: AsyncSession) -> AuditHeadValue:
        """Lock the global head, including a safe first-writer initialization."""
        head_model: AuditHeadModel = self.models.head
        head: AuditHeadValue | None = await session.scalar(
            select(head_model).where(head_model.id == 1).with_for_update()
        )
        if head is None:
            try:
                async with session.begin_nested():
                    session.add(head_model(id=1, seq=0, hash=GENESIS_HASH))
                    await session.flush()
            except IntegrityError:
                pass
            head = await session.scalar(
                select(head_model).where(head_model.id == 1).with_for_update()
            )
        if head is None:
            raise TelescreenError(ErrorCode.CONFLICT, "audit head unavailable")
        return head

    async def _append(
        self,
        session: AsyncSession,
        entry: AuditEntry,
        head: AuditHeadValue,
        at: datetime,
    ) -> int:
        """Append one entry while holding the chain head until transaction commit."""
        seq: int = head.seq + 1
        masked: AuditEntry = redact_audit_entry(entry, self.redactor)
        summary: str = masked.summary
        details_json: str = dump_json(masked.details)
        record_id: str = new_id()
        chain_hash: str = compute_hash(
            head.hash,
            seq,
            record_id,
            at,
            entry.actor_kind.value,
            entry.actor_ref,
            entry.action,
            entry.target_kind,
            entry.target_id,
            summary,
            details_json,
        )
        session.add(
            self.models.record(
                seq=seq,
                id=record_id,
                at=at,
                actor_kind=entry.actor_kind.value,
                actor_ref=entry.actor_ref,
                action=entry.action,
                target_kind=entry.target_kind,
                target_id=entry.target_id,
                summary=summary,
                details_json=details_json,
                prev_hash=head.hash,
                hash=chain_hash,
            )
        )
        head.seq = seq
        head.hash = chain_hash
        await session.flush()
        return seq
