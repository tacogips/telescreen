"""Column limits that SQLite does not enforce are checked by the SQL task queue."""

from datetime import UTC, datetime

import pytest

from telescreen.core.clock import FakeClock
from telescreen.core.json import json_object
from telescreen.core.vocabulary import EngineTaskKind
from telescreen.ports.workflow import TaskSpec
from telescreen.workflow.queue.sql_queue import (
    IDEMPOTENCY_KEY_MAX_LENGTH,
    REF_ID_MAX_LENGTH,
    SqlTaskQueue,
)
from tests.support.db import SqliteHarness, make_sqlite_harness

NOW: datetime = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


def _spec(ref_id: str, key: str) -> TaskSpec:
    """Build a minimal task spec."""
    return TaskSpec(EngineTaskKind.WEEKLY_REPORT, ref_id, json_object(()), NOW, key)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ref_id", "key"),
    (
        ("r" * (REF_ID_MAX_LENGTH + 1), "key-1"),
        ("ref-1", "k" * (IDEMPOTENCY_KEY_MAX_LENGTH + 1)),
    ),
)
async def test_enqueue_rejects_values_longer_than_the_columns(ref_id: str, key: str) -> None:
    """A value PostgreSQL would reject fails the same way under SQLite."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        async with harness.sessions.begin() as session:
            with pytest.raises(ValueError, match="exceeds"):
                await SqlTaskQueue(FakeClock(NOW)).enqueue(session, _spec(ref_id, key))
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_enqueue_accepts_values_at_the_column_limits() -> None:
    """Values exactly at the limits are stored."""
    harness: SqliteHarness = await make_sqlite_harness()
    try:
        async with harness.sessions.begin() as session:
            queued: bool = await SqlTaskQueue(FakeClock(NOW)).enqueue(
                session, _spec("r" * REF_ID_MAX_LENGTH, "k" * IDEMPOTENCY_KEY_MAX_LENGTH)
            )
        assert queued is True
    finally:
        await harness.close()
