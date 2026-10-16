"""Prove Inventory reads and overdue delivery while Telescreen is stopped."""

import subprocess
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from tests.split.support import (
    ServiceStack,
    SmtpRecorder,
    run_cli,
    scalar_int,
    wait_for,
    wait_ready,
)


@pytest.mark.asyncio
async def test_inventory_serves_and_delivers_with_telescreen_stopped(tmp_path: Path) -> None:
    """Keep direct Inventory reads and overdue notifications independent of Telescreen."""
    stack: ServiceStack = ServiceStack.create(tmp_path)
    smtp: SmtpRecorder = SmtpRecorder(stack.smtp_port)
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as http:
            admin_token: str = await stack.start_all(http)
            api_url: str = f"http://127.0.0.1:{stack.api_port}"
            member: httpx.Response = await http.post(
                f"{api_url}/api/members",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={
                    "handle": "alice",
                    "display_name": "Alice",
                    "email": "alice@example.invalid",
                    "slack_user_id": None,
                },
            )
            assert member.status_code == 201, member.text
            member_id: str = member.json()["id"]
            _ = run_cli(
                ("telescreen", "projections", "resync"),
                stack.main_env
                + (
                    (
                        "TELESCREEN_INVENTORY_SERVICE_URL",
                        f"http://127.0.0.1:{stack.inventory_port}",
                    ),
                    (
                        "TELESCREEN_NOTIFICATION_SERVICE_URL",
                        f"http://127.0.0.1:{stack.notification_port}",
                    ),
                    ("TELESCREEN_INVENTORY_SERVICE_TOKEN", "env:P08_INVENTORY_TOKEN"),
                    ("P08_INVENTORY_TOKEN", "inventory-process-token"),
                    ("TELESCREEN_NOTIFICATION_SERVICE_TOKEN", "env:P08_NOTIFICATION_TOKEN"),
                    ("P08_NOTIFICATION_TOKEN", "notification-process-token"),
                ),
            )
            wait_for(
                lambda: (
                    scalar_int(
                        stack.inventory_path,
                        "SELECT count(*) FROM inventory_directory_entries "
                        "WHERE kind='member' AND id=? AND present=1",
                        (member_id,),
                    )
                    == 1
                ),
                90,
                "Inventory directory replica did not receive alice",
            )
            wait_for(
                lambda: (
                    scalar_int(
                        stack.notification_path,
                        "SELECT count(*) FROM notification_directory_entries "
                        "WHERE kind='member' AND id=? AND present=1",
                        (member_id,),
                    )
                    == 1
                ),
                90,
                "Notification directory replica did not receive alice",
            )
            for key, field_type, role in (
                ("deletion_due", "date", "deletion_due"),
                ("owner", "reference", "owner"),
            ):
                field_response: httpx.Response = await http.post(
                    f"{api_url}/api/data/fields",
                    headers={"Authorization": f"Bearer {admin_token}"},
                    json={
                        "key": key,
                        "label": key,
                        "field_type": field_type,
                        "role": role,
                    },
                )
                assert field_response.status_code == 201, field_response.text
            due: str = (date.today() - timedelta(days=7)).isoformat()
            asset: httpx.Response = await http.post(
                f"{api_url}/api/data/assets",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={
                    "name": "Overdue process asset",
                    "source": "manual",
                    "locator": "process/overdue",
                    "kind": "table",
                    "values": [
                        {"key": "deletion_due", "value_json": f'"{due}"'},
                        {"key": "owner", "value_json": '"member:alice"'},
                    ],
                },
            )
            assert asset.status_code == 201, asset.text
            asset_id: str = asset.json()["id"]
            stack.stop("api")
            stack.stop("worker")
            assert stack.api_process is None
            assert stack.worker_process is None
            inventory: subprocess.Popen[bytes] = stack.restart(
                "inventory", (("TELESCREEN_DATA_OVERDUE_CRON", "* * * * *"),)
            )
            inventory_url: str = f"http://127.0.0.1:{stack.inventory_port}"
            # A restarted process accepts connections only once it is ready.
            await wait_ready(
                http,
                f"{inventory_url}/internal/inventory/v1/health",
                inventory,
                "inventory-process-token",
            )
            listing: httpx.Response = await http.get(
                f"{inventory_url}/api/data/assets",
                headers={
                    "Authorization": f"Bearer {admin_token}",
                    "x-telescreen-service-token": "inventory-process-token",
                },
            )
            assert listing.status_code == 200, listing.text
            assert any(item["id"] == asset_id for item in listing.json()["items"])
            detail: httpx.Response = await http.get(
                f"{inventory_url}/api/data/assets/{asset_id}",
                headers={
                    "Authorization": f"Bearer {admin_token}",
                    "x-telescreen-service-token": "inventory-process-token",
                },
            )
            assert detail.status_code == 200, detail.text
            assert detail.json()["id"] == asset_id
            wait_for(
                lambda: (
                    scalar_int(
                        stack.inventory_path,
                        "SELECT count(*) FROM inventory_notification_outbox "
                        "WHERE delivered_at IS NOT NULL",
                    )
                    >= 1
                ),
                90,
                "overdue notification outbox was not delivered",
            )
            wait_for(
                lambda: (
                    scalar_int(
                        stack.notification_path,
                        "SELECT count(*) FROM notifications "
                        "WHERE event_kind='data_overdue' AND status='sent'",
                    )
                    == 1
                ),
                90,
                "Notification did not send the overdue message",
            )
            wait_for(
                lambda: len(smtp.messages) == 1, 90, "SMTP did not receive the overdue message"
            )
            assert "alice@example.invalid" in smtp.messages[0]
            assert (
                scalar_int(
                    stack.inventory_path,
                    "SELECT count(*) FROM inventory_audit_records "
                    "WHERE action='data_asset.overdue_notified'",
                )
                >= 1
            )
            assert inventory.poll() is None
    finally:
        stack.stop_all()
        smtp.close()
