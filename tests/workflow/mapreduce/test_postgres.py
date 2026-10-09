"""PostgreSQL concurrency tests for the global map dispatch cap."""

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from telescreen.core.clock import FakeClock
from telescreen.core.ids import new_id
from telescreen.core.json import dump_json
from telescreen.core.vocabulary import MapItemStatus, MapOrigin
from telescreen.db.base import Base
from telescreen.db.models import MapItemRow, MapJobRow
from telescreen.db.session import create_engine, create_sessionmaker
from telescreen.workflow.mapreduce.dispatch import MapDispatchHandler
from telescreen.workflow.mapreduce.service import spec_to_json
from tests.workflow.mapreduce.test_mapreduce import (
    ConfigFake,
    GatewayFake,
    QueueFake,
    ScanJobsFake,
    _config,
    _spec,
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_dispatchers_observe_global_concurrency_cap() -> None:
    """Two concurrent PostgreSQL dispatchers cannot exceed 32 in-flight shards."""
    database_url: str | None = os.environ.get("TELESCREEN_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("requires TELESCREEN_TEST_DATABASE_URL")
    engine: AsyncEngine = create_engine(database_url)
    sessions: async_sessionmaker[AsyncSession] = create_sessionmaker(engine)
    now: datetime = datetime.now(UTC)
    job_id: str = new_id()
    queue: QueueFake = QueueFake(set(), [])
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions.begin() as session:
            session.add(
                MapJobRow(
                    id=job_id,
                    origin=MapOrigin.COLLECTOR.value,
                    step_run_id=None,
                    collector_config_id="collector-1",
                    spec_json=dump_json(spec_to_json(_spec())),
                    status="running",
                    partition_done=True,
                    max_concurrency=32,
                    paused_until=None,
                    deadline=now + timedelta(hours=1),
                    result_json=None,
                    created_at=now,
                    finished_at=None,
                )
            )
            session.add_all(
                MapItemRow(
                    id=new_id(),
                    map_job_id=job_id,
                    shard_key=f"shard-{index}",
                    descriptor_json='{"id":"opaque"}',
                    weight=1.0,
                    status=MapItemStatus.READY.value,
                    attempt=0,
                    dispatched_at=None,
                    finished_at=None,
                    summary_json=None,
                    error_code=None,
                    police_task_id=None,
                    scan_job_id=None,
                )
                for index in range(100)
            )
        first_handler: MapDispatchHandler = MapDispatchHandler(
            sessions,
            queue,
            GatewayFake(),
            ScanJobsFake(),
            ConfigFake(_config()),
            FakeClock(now),
            2,
        )
        second_handler: MapDispatchHandler = MapDispatchHandler(
            sessions,
            queue,
            GatewayFake(),
            ScanJobsFake(),
            ConfigFake(_config()),
            FakeClock(now),
            2,
        )
        handlers: tuple[MapDispatchHandler, MapDispatchHandler] = (
            first_handler,
            second_handler,
        )
        await asyncio.gather(*(handler._handle_id(job_id) for handler in handlers))
        async with sessions() as session:
            in_flight: int = int(
                await session.scalar(
                    select(func.count())
                    .select_from(MapItemRow)
                    .where(
                        MapItemRow.map_job_id == job_id,
                        MapItemRow.status == MapItemStatus.DISPATCHED.value,
                    )
                )
                or 0
            )
            assert in_flight == 32
    finally:
        async with sessions.begin() as session:
            await session.execute(delete(MapItemRow).where(MapItemRow.map_job_id == job_id))
            await session.execute(delete(MapJobRow).where(MapJobRow.id == job_id))
        await engine.dispose()
