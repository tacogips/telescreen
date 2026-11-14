"""Audit append service."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.audit.chain import GENESIS_HASH, compute_hash
from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.ids import new_id
from telescreen.core.json import (
    JsonKind,
    JsonValue,
    dump_json,
    json_array,
    json_object,
    json_str,
)
from telescreen.db.models import AuditChainHeadRow, AuditRecordRow
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog


def _redact_json(value: JsonValue, redactor: Redactor) -> JsonValue:
    """Redact string leaves recursively without changing JSON structure."""
    if value.kind is JsonKind.STRING:
        return json_str(redactor.redact(value.str_value).text)
    if value.kind is JsonKind.ARRAY:
        return json_array(tuple(_redact_json(item, redactor) for item in value.items))
    if value.kind is JsonKind.OBJECT:
        members: list[tuple[str, JsonValue]] = [
            (member.name, _redact_json(member.value, redactor)) for member in value.members
        ]
        return json_object(members)
    return value


@dataclass(frozen=True)
class AuditLogService(AuditLog):
    """Append masked entries to a serialized, hash-chained audit log."""

    clock: Clock
    redactor: Redactor

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Append one masked event within the caller's transaction."""
        head: AuditChainHeadRow | None = await session.scalar(
            select(AuditChainHeadRow).where(AuditChainHeadRow.id == 1).with_for_update()
        )
        if head is None:
            head = AuditChainHeadRow(id=1, seq=0, hash=GENESIS_HASH)
            session.add(head)
            await session.flush()
        seq: int = head.seq + 1
        at: datetime = ensure_utc(self.clock.now())
        summary: str = self.redactor.redact(entry.summary).text
        details: JsonValue = _redact_json(entry.details, self.redactor)
        details_json: str = dump_json(details)
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
            AuditRecordRow(
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
