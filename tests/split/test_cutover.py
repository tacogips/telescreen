"""Offline CLI proof for notification-first split cutover."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from telescreen.cli import main
from telescreen.db.models import NotificationRow, RetentionNotificationReceiptRow
from telescreen.db.ownership import Store
from tests.support.db import SqliteHarness, make_sqlite_harness


@pytest.mark.asyncio
async def test_notification_cutover_copies_pending_row_and_retention_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run copy and verify before inventory cutover and preserve dedup state."""
    shared: SqliteHarness = await make_sqlite_harness(tmp_path / "shared.db")
    notification: SqliteHarness = await make_sqlite_harness(
        tmp_path / "notification.db", store=Store.NOTIFICATION
    )
    try:
        async with shared.sessions.begin() as session:
            session.add(
                NotificationRow(
                    id="queued-before-cutover",
                    channel="email",
                    recipient_ref="member:owner",
                    subject="Pending notification",
                    body="Safe test message",
                    dlp_verdict="clear",
                    dlp_hit_count=0,
                    status="pending",
                    delivery_attempts=0,
                    run_id=None,
                    issue_id=None,
                    created_at=datetime(2026, 10, 16, tzinfo=UTC),
                    sent_at=None,
                    event_kind="data_overdue",
                    template_name=None,
                    template_version=None,
                    set_name=None,
                    target_index=None,
                    error_code=None,
                    category=None,
                )
            )
            session.add(
                RetentionNotificationReceiptRow(
                    id="retention-obligation",
                    payload_sha256="a" * 64,
                    applied_at=datetime(2026, 10, 16, tzinfo=UTC),
                )
            )
        monkeypatch.setenv("TELESCREEN_DATABASE_URL", str(shared.engine.url))
        monkeypatch.setenv("TELESCREEN_NOTIFICATION_DATABASE_URL", str(notification.engine.url))
        assert (
            await asyncio.to_thread(main, ["notification-store", "copy", "--writers-stopped"]) == 0
        )
        assert (
            await asyncio.to_thread(main, ["notification-store", "verify", "--writers-stopped"])
            == 0
        )
        async with notification.sessions() as session:
            rows: int = int(
                await session.scalar(select(func.count()).select_from(NotificationRow)) or 0
            )
            receipts: int = int(
                await session.scalar(
                    select(func.count()).select_from(RetentionNotificationReceiptRow)
                )
                or 0
            )
            copied: NotificationRow | None = await session.get(
                NotificationRow, "queued-before-cutover"
            )
        assert rows == 1
        assert receipts == 1
        assert copied is not None and copied.status == "pending"
    finally:
        await notification.close()
        await shared.close()
