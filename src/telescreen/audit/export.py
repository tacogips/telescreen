"""Ordered JSON-lines export for one parameterized audit chain."""

import json
from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.engine import ScalarResult
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.audit.chain import AuditChainModels, AuditHeadValue, AuditRecordValue


async def export_chain(session: AsyncSession, models: AuditChainModels) -> AsyncIterator[str]:
    """Yield stored audit rows followed by a chain-head summary line."""
    rows: ScalarResult[AuditRecordValue] = await session.scalars(
        select(models.record).order_by(models.record.seq)
    )
    for row in rows:
        yield json.dumps(
            {
                "seq": row.seq,
                "id": row.id,
                "at": row.at.isoformat(),
                "actor_kind": row.actor_kind,
                "actor_ref": row.actor_ref,
                "action": row.action,
                "target_kind": row.target_kind,
                "target_id": row.target_id,
                "summary": row.summary,
                "details_json": row.details_json,
                "prev_hash": row.prev_hash,
                "hash": row.hash,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    head: AuditHeadValue | None = await session.get(models.head, 1)
    yield json.dumps(
        {
            "head_seq": head.seq if head is not None else 0,
            "head_hash": head.hash if head is not None else "0" * 64,
        },
        separators=(",", ":"),
    )
