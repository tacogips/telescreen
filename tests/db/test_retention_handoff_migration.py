"""Incremental retention migration preserves master values and prior notification markers."""

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import Session

from telescreen.core.clock import FakeClock
from telescreen.core.json import json_null
from telescreen.core.vocabulary import DataFieldRole, EngineTaskKind
from telescreen.db.models import DataAssetRow, DataFieldValueRow, InventoryRetentionOutboxRow
from telescreen.db.session import create_sessionmaker
from telescreen.inventory.retention import DataOverdueHandler
from telescreen.masking.redact import Redactor
from telescreen.ports.workflow import ClaimedTask
from tests.db.test_v14_migration import _alembic_config
from tests.inventory.handoff.conftest import NOW
from tests.inventory.retention.helpers import _asset, _field


async def publish_due(url: str) -> None:
    """Run the production inventory publisher against the upgraded database."""
    engine: AsyncEngine = create_async_engine(url.replace("sqlite://", "sqlite+aiosqlite://"))
    try:
        await DataOverdueHandler(
            create_sessionmaker(engine), Redactor.default(), FakeClock(NOW), None
        ).handle(
            ClaimedTask("check", EngineTaskKind.DATA_OVERDUE_CHECK, "due", json_null(), 1, 5, "due")
        )
    finally:
        await engine.dispose()


def test_legacy_values_are_backfilled_without_resending_notified_dates(tmp_path: Path) -> None:
    """Revision backfill is stable, keeps old values, and downgrade guards pending intent."""
    url: str = f"sqlite:///{tmp_path / 'retention-migration.db'}"
    config: Config = _alembic_config(url)
    command.upgrade(config, "0007_inventory_audit")
    engine: Engine = create_engine(url)
    try:
        assert "revision_id" not in {
            column["name"] for column in inspect(engine).get_columns("data_field_values")
        }
        with Session(engine) as session:
            first: DataAssetRow = _asset("already-notified", 1, NOW)
            first.overdue_notified_for = "2026-10-12"
            session.add(first)
            session.add(_asset("not-yet-notified", 2, NOW))
            session.add(_field("due", "deletion_due", DataFieldRole.DELETION_DUE, "date"))
            session.commit()
        with engine.begin() as connection:
            for asset_id in ("already-notified", "not-yet-notified"):
                connection.execute(
                    text(
                        "INSERT INTO data_field_values (asset_id, field_id, value_json, "
                        "value_date, updated_by, updated_at) "
                        "VALUES (:asset, 'due', :value, '2026-10-12', 'member:admin', :at)"
                    ),
                    {"asset": asset_id, "value": '"2026-10-12"', "at": NOW.isoformat()},
                )
        command.upgrade(config, "head")
        with Session(engine) as session:
            values: tuple[DataFieldValueRow, ...] = tuple(
                session.scalars(select(DataFieldValueRow)).all()
            )
            revisions: tuple[str, ...] = tuple(sorted(value.revision_id for value in values))
            assert len(set(revisions)) == 2 and all(len(value) == 36 for value in revisions)
            assert all(value.value_json == '"2026-10-12"' for value in values)
        command.stamp(config, "0007_inventory_audit")
        command.upgrade(config, "head")
        with Session(engine) as session:
            assert (
                tuple(sorted(session.scalars(select(DataFieldValueRow.revision_id)).all()))
                == revisions
            )
        asyncio.run(publish_due(url))
        with Session(engine) as session:
            pending: tuple[InventoryRetentionOutboxRow, ...] = tuple(
                session.scalars(select(InventoryRetentionOutboxRow)).all()
            )
            assert len(pending) == 1 and "not-yet-notified" in pending[0].payload_json
        with pytest.raises(RuntimeError, match="drain inventory retention outbox"):
            command.downgrade(config, "0007_inventory_audit")
        with Session(engine) as session:
            row: InventoryRetentionOutboxRow | None = session.scalar(
                select(InventoryRetentionOutboxRow)
            )
            assert row is not None
            row.delivered_at = NOW
            session.commit()
        command.downgrade(config, "0007_inventory_audit")
        assert "revision_id" not in {
            column["name"] for column in inspect(engine).get_columns("data_field_values")
        }
        assert "inventory_retention_outbox" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        asyncio.run(publish_due(url))
        with Session(engine) as session:
            assert session.scalar(select(InventoryRetentionOutboxRow)) is None
            assert len(tuple(session.scalars(select(DataAssetRow)))) == 2
    finally:
        engine.dispose()
