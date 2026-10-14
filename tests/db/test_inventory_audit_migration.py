"""Incremental inventory audit migration preserves the asset master and existing chain."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from telescreen.audit.chain import GENESIS_HASH, compute_hash
from telescreen.db.models import (
    AuditChainHeadRow,
    AuditRecordRow,
    DataAssetRow,
    InventoryAuditOutboxRow,
)
from tests.db.test_v14_migration import _alembic_config
from tests.inventory.core.test_query_contract import _asset
from tests.inventory.handoff.conftest import NOW


def test_audit_handoff_upgrade_and_guarded_downgrade(tmp_path: Path) -> None:
    """Never discard pending audit intent, or rewrite master IDs and chain hashes."""
    url: str = f"sqlite:///{tmp_path / 'audit-migration.db'}"
    config: Config = _alembic_config(url)
    command.upgrade(config, "0006_inventory_handoff")
    engine: Engine = create_engine(url)
    digest: str = compute_hash(
        GENESIS_HASH,
        1,
        "audit",
        NOW,
        "system",
        "system:test",
        "created",
        "data_asset",
        "asset",
        "Created",
        "null",
    )
    try:
        with Session(engine) as session:
            session.add(_asset("asset", 1, "Master", NOW))
            session.add(
                AuditRecordRow(
                    seq=1,
                    id="audit",
                    at=NOW,
                    actor_kind="system",
                    actor_ref="system:test",
                    action="created",
                    target_kind="data_asset",
                    target_id="asset",
                    summary="Created",
                    details_json="null",
                    prev_hash=GENESIS_HASH,
                    hash=digest,
                )
            )
            head: AuditChainHeadRow | None = session.get(AuditChainHeadRow, 1)
            assert head is not None
            head.seq = 1
            head.hash = digest
            session.commit()
        command.upgrade(config, "head")
        command.stamp(config, "0006_inventory_handoff")
        command.upgrade(config, "head")
        with Session(engine) as session:
            session.add(InventoryAuditOutboxRow(id="pending", payload_json="{}", recorded_at=NOW))
            session.commit()
        with pytest.raises(RuntimeError, match="drain inventory audit outbox"):
            command.downgrade(config, "0006_inventory_handoff")
        assert "inventory_audit_outbox" in inspect(engine).get_table_names()
        with Session(engine) as session:
            row: InventoryAuditOutboxRow | None = session.get(InventoryAuditOutboxRow, "pending")
            assert row is not None
            row.delivered_at = NOW
            session.commit()
        command.downgrade(config, "0006_inventory_handoff")
        assert "inventory_audit_outbox" not in inspect(engine).get_table_names()
        assert "audit_receipts" not in inspect(engine).get_table_names()
        with Session(engine) as session:
            assert session.scalar(select(AuditRecordRow.hash)) == digest
            assert session.scalar(select(DataAssetRow.name)) == "Master"
            head = session.get(AuditChainHeadRow, 1)
            assert head is not None and head.seq == 1 and head.hash == digest
        command.upgrade(config, "head")
    finally:
        engine.dispose()
