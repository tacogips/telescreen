"""Audit-chain integrity verification."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.audit.chain import (
    GENESIS_HASH,
    MAIN_AUDIT_MODELS,
    AuditChainModels,
    AuditHeadValue,
    AuditRecordValue,
    compute_hash,
)
from telescreen.core.clock import ensure_utc


@dataclass(frozen=True)
class ChainVerification:
    """Result of checking the stored audit chain."""

    ok: bool
    first_broken_seq: int | None
    checked: int


async def verify_chain(
    session: AsyncSession, models: AuditChainModels = MAIN_AUDIT_MODELS
) -> ChainVerification:
    """Recompute the audit chain in bounded pages and verify its head pointer."""
    previous_hash: str = GENESIS_HASH
    expected_seq: int = 1
    checked: int = 0
    while True:
        rows: list[AuditRecordValue] = list(
            (
                await session.scalars(
                    select(models.record)
                    .where(models.record.seq >= expected_seq)
                    .order_by(models.record.seq)
                    .limit(1000)
                )
            ).all()
        )
        if not rows:
            break
        for row in rows:
            checked += 1
            if row.seq != expected_seq or row.prev_hash != previous_hash:
                return ChainVerification(False, expected_seq, checked)
            expected_hash: str = compute_hash(
                previous_hash,
                row.seq,
                row.id,
                ensure_utc(row.at),
                row.actor_kind,
                row.actor_ref,
                row.action,
                row.target_kind,
                row.target_id,
                row.summary,
                row.details_json,
            )
            if expected_hash != row.hash:
                return ChainVerification(False, row.seq, checked)
            previous_hash = row.hash
            expected_seq += 1
        if len(rows) < 1000:
            break
    head: AuditHeadValue | None = await session.scalar(
        select(models.head).where(models.head.id == 1)
    )
    if head is None:
        return ChainVerification(checked == 0, None if checked == 0 else 1, checked)
    if head.seq != checked or head.hash != previous_hash:
        return ChainVerification(False, max(1, min(head.seq, checked + 1)), checked)
    return ChainVerification(True, None, checked)
