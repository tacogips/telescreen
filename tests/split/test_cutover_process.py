"""Prove notification-store cutover delivery and retained-obligation dedup over TCP."""

import asyncio
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from telescreen.core.vocabulary import DataAssetState
from telescreen.db.models import (
    DataAssetRow,
    DataFieldRow,
    DataFieldValueRow,
    MemberRow,
    NotificationRow,
    RetentionNotificationReceiptRow,
)
from telescreen.ports.retention import retention_event_id
from tests.split.support import (
    ServiceStack,
    SmtpRecorder,
    run_cli,
    scalar_int,
    wait_for,
    wait_ready,
)


@pytest.mark.asyncio
async def test_notification_cutover_process_delivers_once_and_deduplicates_retention(
    tmp_path: Path,
) -> None:
    """Copy pending notification state, then prove a copied receipt suppresses a replay."""
    stack: ServiceStack = ServiceStack.create(tmp_path)
    smtp: SmtpRecorder = SmtpRecorder(stack.smtp_port)
    processes: list[subprocess.Popen[bytes]] = []
    main_only: tuple[tuple[str, str], ...] = tuple(
        pair for pair in stack.main_env if pair[0] != "TELESCREEN_NOTIFICATION_DATABASE_URL"
    )
    now: datetime = datetime.now(UTC)
    alice_id: str = "p08-alice-00000000-0000-000000000001"
    asset_id: str = "p08-asset-00000000-0000-000000000001"
    due_field_id: str = "p08-due-00000000-0000-000000000001"
    owner_field_id: str = "p08-owner-00000000-0000-000000000001"
    due_revision_id: str = "p08-revision-00000000-000000000001"
    due: str = (date.today() - timedelta(days=7)).isoformat()
    event_id: str = retention_event_id(asset_id, due_field_id, due_revision_id)
    try:
        _ = run_cli(("telescreen", "migrate", "--store", "main"), main_only)
        _ = run_cli(("telescreen.inventory_service", "migrate"), stack.inventory_env)
        main_engine: AsyncEngine = create_async_engine(f"sqlite+aiosqlite:///{stack.main_path}")
        inventory_engine: AsyncEngine = create_async_engine(
            f"sqlite+aiosqlite:///{stack.inventory_path}"
        )
        try:
            main_sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(main_engine)
            inventory_sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
                inventory_engine
            )
            async with main_sessions.begin() as session:
                session.add(
                    MemberRow(
                        id=alice_id,
                        handle="alice",
                        display_name="Alice",
                        email="alice@example.invalid",
                        slack_user_id=None,
                        active=True,
                        created_at=now,
                        updated_at=now,
                        role="admin",
                        last_sign_in_at=None,
                    )
                )
                session.add(
                    NotificationRow(
                        id="p08-cutover-pending-00000000000001",
                        channel="email",
                        recipient_ref=f"member:{alice_id}",
                        subject="P08 copied pending notification",
                        body="Safe cutover message",
                        dlp_verdict="clear",
                        dlp_hit_count=0,
                        status="pending",
                        delivery_attempts=0,
                        run_id=None,
                        issue_id=None,
                        created_at=now,
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
                        id=event_id,
                        payload_sha256="a" * 64,
                        applied_at=now,
                    )
                )
            async with inventory_sessions.begin() as session:
                session.add_all(
                    (
                        DataFieldRow(
                            id=due_field_id,
                            key="deletion_due",
                            label="Deletion due",
                            description=None,
                            field_type="date",
                            options_json=None,
                            required=False,
                            position=0,
                            role="deletion_due",
                            archived_at=None,
                            created_by="member:admin",
                            created_at=now,
                            updated_at=now,
                        ),
                        DataFieldRow(
                            id=owner_field_id,
                            key="owner",
                            label="Owner",
                            description=None,
                            field_type="reference",
                            options_json=None,
                            required=False,
                            position=1,
                            role="owner",
                            archived_at=None,
                            created_by="member:admin",
                            created_at=now,
                            updated_at=now,
                        ),
                    )
                )
                session.add(
                    DataAssetRow(
                        id=asset_id,
                        number=1,
                        resource_key="a" * 64,
                        source="manual",
                        origin="registered",
                        kind="file",
                        state=DataAssetState.PRESENT.value,
                        name="P08 overdue cutover asset",
                        display_locator="https://example.test/p08-cutover",
                        collector_config_id=None,
                        sweep_covered=False,
                        item_count=None,
                        first_seen_at=now,
                        last_seen_at=now,
                        deleted_at=None,
                        overdue_notified_for=None,
                        created_by="member:admin",
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.add_all(
                    (
                        DataFieldValueRow(
                            asset_id=asset_id,
                            field_id=due_field_id,
                            value_json=f'"{due}"',
                            value_date=due,
                            updated_by="member:admin",
                            updated_at=now,
                            revision_id=due_revision_id,
                        ),
                        DataFieldValueRow(
                            asset_id=asset_id,
                            field_id=owner_field_id,
                            value_json='"member:alice"',
                            value_date=None,
                            updated_by="member:admin",
                            updated_at=now,
                        ),
                    )
                )
        finally:
            await main_engine.dispose()
            await inventory_engine.dispose()
        _ = run_cli(("telescreen.notification_service", "migrate"), stack.notification_env)
        copy_env: tuple[tuple[str, str], ...] = main_only + (
            (
                "TELESCREEN_NOTIFICATION_DATABASE_URL",
                f"sqlite+aiosqlite:///{stack.notification_path}",
            ),
        )
        _ = run_cli(("telescreen", "notification-store", "copy", "--writers-stopped"), copy_env)
        _ = run_cli(("telescreen", "notification-store", "verify", "--writers-stopped"), copy_env)
        stack.admin_token = run_cli(
            (
                "telescreen",
                "token",
                "create",
                "--label",
                "p08-cutover",
                "--scopes",
                "admin,read,write",
            ),
            main_only,
        )
        notification: subprocess.Popen[bytes] = stack.start("notification")
        inventory: subprocess.Popen[bytes] = stack.start("inventory")
        api: subprocess.Popen[bytes] = stack.start("api")
        worker: subprocess.Popen[bytes] = stack.start("worker")
        processes.extend((notification, inventory, api, worker))
        async with httpx.AsyncClient(timeout=3, trust_env=False) as http:
            await wait_ready(
                http,
                f"http://127.0.0.1:{stack.notification_port}/internal/notification/v1/health",
                notification,
                "notification-process-token",
            )
            await wait_ready(
                http,
                f"http://127.0.0.1:{stack.inventory_port}/internal/inventory/v1/health",
                inventory,
                "inventory-process-token",
            )
            await wait_ready(http, f"http://127.0.0.1:{stack.api_port}/api/health", api)
            _ = run_cli(("telescreen", "projections", "resync"), stack.main_runtime())
            wait_for(
                lambda: (
                    scalar_int(
                        stack.notification_path,
                        "SELECT count(*) FROM notifications WHERE id=? AND status='sent'",
                        ("p08-cutover-pending-00000000000001",),
                    )
                    == 1
                ),
                90,
                "copied pending notification was not delivered",
            )
            wait_for(
                lambda: len(smtp.messages) == 1,
                90,
                "SMTP did not receive the copied pending notification",
            )
            assert smtp.messages[0].count("P08 copied pending notification") == 1
            await asyncio.sleep(5)
            assert len(smtp.messages) == 1
            before_overdue: int = scalar_int(
                stack.notification_path,
                "SELECT count(*) FROM notifications WHERE event_kind='data_overdue'",
            )
            inventory = stack.restart("inventory", (("TELESCREEN_DATA_OVERDUE_CRON", "* * * * *"),))
            await wait_ready(
                http,
                f"http://127.0.0.1:{stack.inventory_port}/internal/inventory/v1/health",
                inventory,
                "inventory-process-token",
            )
            wait_for(
                lambda: (
                    scalar_int(
                        stack.inventory_path,
                        "SELECT count(*) FROM inventory_notification_outbox "
                        "WHERE id=? AND delivered_at IS NOT NULL",
                        (event_id,),
                    )
                    == 1
                ),
                90,
                "copied retention receipt did not receive the overdue replay",
            )
            assert (
                scalar_int(
                    stack.inventory_path,
                    "SELECT count(*) FROM inventory_notification_outbox WHERE id=?",
                    (event_id,),
                )
                == 1
            )
            assert (
                scalar_int(
                    stack.notification_path,
                    "SELECT count(*) FROM notification_inbox WHERE id=?",
                    (event_id,),
                )
                == 0
            )
            assert (
                scalar_int(
                    stack.notification_path,
                    "SELECT count(*) FROM notifications WHERE event_kind='data_overdue'",
                )
                == before_overdue
            )
            assert len(smtp.messages) == 1
    finally:
        stack.stop_all()
        smtp.close()
