"""Prove durable producer command replay across stopped service processes."""

import subprocess
from pathlib import Path

import httpx
import pytest

from telescreen.core.vocabulary import IssueStatus, NotificationEvent
from telescreen.ports.inventory_commands import (
    InventoryCommand,
    IssueLinkState,
    decode_inventory_command,
)
from telescreen.ports.notification_commands import (
    MemberSendOperation,
    NotificationCommand,
    decode_notification_command,
)
from tests.split.support import (
    ServiceStack,
    SmtpRecorder,
    fetch_rows,
    scalar_int,
    seed_issue,
    seed_member,
    wait_for,
    wait_ready,
)
from tests.support.samples import aws_access_key_id


@pytest.mark.asyncio
async def test_notification_outage_replays_once(tmp_path: Path) -> None:
    """Apply a mention once after Notification restarts, then prove duplicate receipt."""
    stack: ServiceStack = ServiceStack.create(tmp_path)
    smtp: SmtpRecorder = SmtpRecorder(stack.smtp_port)
    bodies: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as http:
            _ = await stack.start_all(http)
            member_id: str = await seed_member(stack, http, "alice", bodies)
            issue_id: str = await seed_issue(stack, http, aws_access_key_id().value, bodies)
            stack.stop("notification")
            comment: httpx.Response = await http.post(
                f"http://127.0.0.1:{stack.api_port}/api/issues/{issue_id}/comments",
                headers={"Authorization": f"Bearer {stack.admin_token}"},
                json={"body": "Please review @alice"},
            )
            bodies.append(comment.text)
            assert comment.status_code == 201, comment.text
            wait_for(
                lambda: (
                    scalar_int(
                        stack.main_path,
                        "SELECT count(*) FROM notification_outbox WHERE delivered_at IS NULL",
                    )
                    > 0
                    and scalar_int(
                        stack.main_path,
                        "SELECT count(*) FROM engine_tasks "
                        "WHERE kind='notification_command_delivery' AND attempts >= 1",
                    )
                    > 0
                ),
                90,
                "notification command did not fail into the durable retry queue",
            )
            notification: subprocess.Popen[bytes] = stack.restart("notification")
            await wait_ready(
                http,
                f"http://127.0.0.1:{stack.notification_port}/internal/notification/v1/health",
                notification,
                "notification-process-token",
            )
            wait_for(
                lambda: (
                    scalar_int(
                        stack.main_path,
                        "SELECT count(*) FROM notification_outbox WHERE delivered_at IS NULL",
                    )
                    == 0
                ),
                90,
                "notification outbox did not drain after restart",
            )
            outboxes: tuple[tuple[str, ...], ...] = fetch_rows(
                stack.main_path, "notification_outbox"
            )
            assert outboxes
            mention_event: bool = False
            mention_member: bool = False
            for outbox in outboxes:
                command: NotificationCommand = decode_notification_command(outbox[1])
                assert (
                    scalar_int(
                        stack.notification_path,
                        "SELECT count(*) FROM notification_inbox WHERE id=?",
                        (outbox[0],),
                    )
                    == 1
                )
                mention_event = mention_event or any(
                    isinstance(operation, MemberSendOperation)
                    and operation.event is NotificationEvent.ISSUE_MENTIONED
                    for operation in command.operations
                )
                mention_member = mention_member or any(
                    isinstance(operation, MemberSendOperation)
                    and operation.event is NotificationEvent.ISSUE_MENTIONED
                    and operation.member_id == member_id
                    for operation in command.operations
                )
            assert mention_event
            assert mention_member
            mention_count: int = scalar_int(
                stack.notification_path,
                "SELECT count(*) FROM notifications WHERE recipient_ref=? AND event_kind=?",
                ("member:alice", NotificationEvent.ISSUE_MENTIONED.value),
            )
            assert mention_count == 1
            duplicate_id: str = outboxes[-1][0]
            duplicate_payload: str = outboxes[-1][1]
            inbox_before: int = scalar_int(
                stack.notification_path, "SELECT count(*) FROM notification_inbox"
            )
            duplicate: httpx.Response = await http.post(
                f"http://127.0.0.1:{stack.notification_port}/internal/notification/v1/commands",
                content=duplicate_payload,
                headers={"x-telescreen-service-token": "notification-process-token"},
            )
            bodies.append(duplicate.text)
            assert duplicate.status_code == 200, duplicate.text
            assert duplicate.json() == "duplicate"
            assert (
                scalar_int(
                    stack.notification_path,
                    "SELECT count(*) FROM notification_inbox WHERE id=?",
                    (duplicate_id,),
                )
                == 1
            )
            assert (
                scalar_int(stack.notification_path, "SELECT count(*) FROM notification_inbox")
                == inbox_before
            )
            assert (
                scalar_int(
                    stack.notification_path,
                    "SELECT count(*) FROM notifications WHERE recipient_ref=? AND event_kind=?",
                    ("member:alice", NotificationEvent.ISSUE_MENTIONED.value),
                )
                == mention_count
            )
    finally:
        stack.stop_all()
        smtp.close()


@pytest.mark.asyncio
async def test_inventory_outage_replays_once(tmp_path: Path) -> None:
    """Apply a changed issue-link command once after Inventory restarts."""
    stack: ServiceStack = ServiceStack.create(tmp_path)
    smtp: SmtpRecorder = SmtpRecorder(stack.smtp_port)
    bodies: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as http:
            _ = await stack.start_all(http)
            _ = await seed_member(stack, http, "alice", bodies)
            issue_id: str = await seed_issue(stack, http, aws_access_key_id().value, bodies)
            wait_for(
                lambda: (
                    scalar_int(
                        stack.inventory_path,
                        "SELECT count(*) FROM inventory_issue_links WHERE issue_id=?",
                        (issue_id,),
                    )
                    == 1
                    and scalar_int(
                        stack.main_path,
                        "SELECT count(*) FROM inventory_outbox WHERE delivered_at IS NULL",
                    )
                    == 0
                ),
                90,
                "initial issue link was not projected before outage",
            )
            stack.stop("inventory")
            response: httpx.Response = await http.patch(
                f"http://127.0.0.1:{stack.api_port}/api/issues/{issue_id}",
                headers={"Authorization": f"Bearer {stack.admin_token}"},
                json={"status": IssueStatus.IN_PROGRESS.value},
            )
            bodies.append(response.text)
            assert response.status_code == 200, response.text
            wait_for(
                lambda: _has_pending_issue_link(stack, issue_id),
                90,
                "issue-link command did not fail into the durable retry queue",
            )
            inventory: subprocess.Popen[bytes] = stack.restart("inventory")
            await wait_ready(
                http,
                f"http://127.0.0.1:{stack.inventory_port}/internal/inventory/v1/health",
                inventory,
                "inventory-process-token",
            )
            wait_for(
                lambda: (
                    scalar_int(
                        stack.main_path,
                        "SELECT count(*) FROM inventory_outbox WHERE delivered_at IS NULL",
                    )
                    == 0
                ),
                90,
                "inventory outbox did not drain after restart",
            )
            links: tuple[tuple[str, ...], ...] = tuple(
                row
                for row in fetch_rows(stack.inventory_path, "inventory_issue_links")
                if row[0] == issue_id
            )
            assert len(links) == 1 and links[0][3] == IssueStatus.IN_PROGRESS.value
            outboxes: tuple[tuple[str, ...], ...] = fetch_rows(stack.main_path, "inventory_outbox")
            issue_commands: list[InventoryCommand] = []
            for outbox in outboxes:
                candidate: InventoryCommand = decode_inventory_command(outbox[3])
                if (
                    isinstance(candidate.operation, IssueLinkState)
                    and candidate.operation.issue_id == issue_id
                ):
                    issue_commands.append(candidate)
            assert issue_commands
            command: InventoryCommand = issue_commands[-1]
            assert isinstance(command.operation, IssueLinkState)
            assert command.operation.status == IssueStatus.IN_PROGRESS.value
            assert scalar_int(
                stack.inventory_path,
                "SELECT last_sequence FROM inventory_inbox_streams WHERE id=?",
                (command.stream_id,),
            ) == scalar_int(
                stack.main_path,
                "SELECT max(sequence) FROM inventory_outbox WHERE stream_id=?",
                (command.stream_id,),
            )
            for outbox in outboxes:
                assert (
                    scalar_int(
                        stack.inventory_path,
                        "SELECT count(*) FROM inventory_inbox WHERE id=?",
                        (outbox[0],),
                    )
                    == 1
                )
    finally:
        stack.stop_all()
        smtp.close()


def _has_pending_issue_link(stack: ServiceStack, issue_id: str) -> bool:
    """Find an undelivered typed issue-link operation for one issue."""
    rows: tuple[tuple[str, ...], ...] = fetch_rows(stack.main_path, "inventory_outbox")
    for row in rows:
        if row[5] != "None":
            continue
        command: InventoryCommand = decode_inventory_command(row[3])
        if isinstance(command.operation, IssueLinkState) and command.operation.issue_id == issue_id:
            return (
                scalar_int(
                    stack.main_path,
                    "SELECT count(*) FROM engine_tasks "
                    "WHERE kind='inventory_delivery' AND ref_id=? AND attempts >= 1",
                    (command.event_id,),
                )
                > 0
            )
    return False
