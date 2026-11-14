"""Replay nonce storage behavior tests."""

from datetime import UTC, datetime, timedelta

import pytest

from telescreen.core.clock import FakeClock
from telescreen.db.replay import ReplayNonceStore
from tests.support.db import SqliteHarness, make_sqlite_harness


@pytest.mark.asyncio
async def test_nonce_is_unique_until_expired_then_can_be_reused() -> None:
    """Accept a nonce once, reject its replay, and accept it after expiry."""
    harness: SqliteHarness = await make_sqlite_harness()
    store: ReplayNonceStore = ReplayNonceStore()
    now: datetime = datetime(2026, 10, 8, tzinfo=UTC)
    clock: FakeClock = FakeClock(current=now)
    expiry: datetime = now + timedelta(minutes=2)
    async with harness.sessions.begin() as session:
        assert await store.remember(session, "webhook", "nonce", expiry, now)
    async with harness.sessions.begin() as session:
        assert not await store.remember(session, "webhook", "nonce", expiry, now)
    clock.advance(timedelta(minutes=2, seconds=1))
    after_expiry: datetime = clock.now()
    async with harness.sessions.begin() as session:
        assert await store.remember(
            session, "webhook", "nonce", after_expiry + timedelta(minutes=2), after_expiry
        )
    await harness.close()
