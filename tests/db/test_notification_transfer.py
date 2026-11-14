"""Notification-store copies expose only value-free manifests and preserve history."""

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pytest

from telescreen.db.models.notifications import NotificationRow
from telescreen.db.models.retention_handoff import RetentionNotificationReceiptRow
from telescreen.db.models.service_audit import NotificationAuditChainHeadRow
from telescreen.db.notification_transfer import (
    NotificationTransferReport,
    transfer_notification_store,
)
from telescreen.db.ownership import Store
from tests.support.db import SqliteHarness, make_sqlite_harness


async def test_notification_split_copy_and_verify_are_value_free(tmp_path: Path) -> None:
    """The copied notification rows are covered by hashes without exposing their contents."""
    main: SqliteHarness = await make_sqlite_harness(tmp_path / "main.db")
    notification: SqliteHarness = await make_sqlite_harness(
        tmp_path / "notification.db", store=Store.NOTIFICATION
    )
    try:
        async with main.sessions.begin() as session:
            session.add(
                NotificationRow(
                    id="notification-id",
                    channel="email",
                    recipient_ref="member:alice",
                    subject="safe subject",
                    body="private notification body",
                    dlp_verdict="clear",
                    dlp_hit_count=0,
                    status="pending",
                    delivery_attempts=0,
                    run_id=None,
                    issue_id=None,
                    created_at=datetime(2026, 10, 15, tzinfo=UTC),
                    sent_at=None,
                    event_kind=None,
                    template_name=None,
                    template_version=None,
                    set_name=None,
                    target_index=None,
                    error_code=None,
                    category=None,
                )
            )
        source_url: str = str(main.engine.url)
        target_url: str = str(notification.engine.url)
        copied: NotificationTransferReport = await transfer_notification_store(
            source_url, target_url, writers_stopped=True
        )
        verified: NotificationTransferReport = await transfer_notification_store(
            source_url, target_url, writers_stopped=True, verify_only=True
        )
        encoded: str = json.dumps(asdict(copied))
        assert copied.copied is True
        assert verified.copied is False
        assert "private notification body" not in encoded
        assert all(len(item.sha256) == 64 for item in copied.tables)
    finally:
        await notification.close()
        await main.close()


async def test_notification_merge_refuses_missing_target_receipt(tmp_path: Path) -> None:
    """Merge leaves main unchanged when the Notification source lost a receipt."""
    main: SqliteHarness = await make_sqlite_harness(tmp_path / "main.db")
    notification: SqliteHarness = await make_sqlite_harness(
        tmp_path / "notification.db", store=Store.NOTIFICATION
    )
    try:
        async with main.sessions.begin() as session:
            session.add(NotificationAuditChainHeadRow(id=1, seq=0, hash="0" * 64))
            session.add(
                RetentionNotificationReceiptRow(
                    id="receipt-id",
                    payload_sha256="a" * 64,
                    applied_at=datetime(2026, 10, 15, tzinfo=UTC),
                )
            )
        with pytest.raises(RuntimeError):
            await transfer_notification_store(
                str(notification.engine.url),
                str(main.engine.url),
                writers_stopped=True,
                merge=True,
            )
        async with main.sessions() as session:
            receipt: RetentionNotificationReceiptRow | None = await session.get(
                RetentionNotificationReceiptRow, "receipt-id"
            )
        assert receipt is not None
    finally:
        await notification.close()
        await main.close()
