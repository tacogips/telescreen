"""Nonempty inventory snapshots including every owner table and durable handoff state."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncEngine

from telescreen.core.vocabulary import DataFieldRole
from telescreen.db.models import (
    DataAssetEventRow,
    DataAssetItemRow,
    DataFieldValueRow,
    InventoryAuditOutboxRow,
    InventoryCounterRow,
    InventoryInboxRow,
    InventoryInboxStreamRow,
    InventoryRetentionOutboxRow,
)
from telescreen.db.session import create_sessionmaker
from tests.inventory.retention.helpers import _asset, _field

NOW: datetime = datetime(2026, 10, 14, 8, 9, 10, 123456, tzinfo=UTC)


async def seed_inventory(engine: AsyncEngine) -> None:
    """Seed actual typed values; manifests must preserve them byte-for-byte after normalization."""
    async with create_sessionmaker(engine).begin() as session:
        session.add(_asset("asset-1", 1, NOW))
        session.add(_asset("asset-2", 2, NOW))
        session.add(_field("due", "deletion_due", DataFieldRole.DELETION_DUE, "date"))
        session.add(
            DataFieldValueRow(
                asset_id="asset-1",
                field_id="due",
                value_json='"2026-10-12"',
                value_date="2026-10-12",
                updated_by="member:owner",
                updated_at=NOW,
                revision_id="original-revision",
            )
        )
        session.add(
            DataAssetEventRow(
                id="event-1",
                asset_id="asset-1",
                kind="registered",
                recorded_at=NOW,
                actor_ref="member:owner",
                note="Café\n東京",
            )
        )
        session.add(
            DataAssetItemRow(
                id="item-1",
                asset_id="asset-1",
                kind="key",
                fingerprint="a" * 64,
                key_version=2,
                first_seen_at=NOW,
            )
        )
        session.add(InventoryInboxStreamRow(id="collector:copy", last_sequence=12))
        for sequence in range(1, 13):
            session.add(
                InventoryInboxRow(
                    id=f"receipt-{sequence}",
                    stream_id="collector:copy",
                    sequence=sequence,
                    payload_sha256=f"{sequence:064x}",
                    applied_at=NOW,
                )
            )
        session.add(
            InventoryAuditOutboxRow(
                id="audit-pending", payload_json='{"opaque":"Café\\n東京"}', recorded_at=NOW
            )
        )
        session.add(
            InventoryRetentionOutboxRow(
                id="retention-pending",
                payload_json='{"opaque":"revision-specific"}',
                recorded_at=NOW,
            )
        )
        counter: InventoryCounterRow | None = await session.get(
            InventoryCounterRow, "data_asset_number"
        )
        assert counter is not None
        counter.value = 31
