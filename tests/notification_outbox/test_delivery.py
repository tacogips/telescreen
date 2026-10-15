"""Notification command HTTP delivery retry and acknowledgment behavior."""

from datetime import UTC, datetime

import httpx
import pytest

from telescreen.core.clock import FakeClock
from telescreen.core.json import json_null
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import EngineTaskKind
from telescreen.db.models.notification_handoff import NotificationOutboxRow
from telescreen.integrations.notification_client import NotificationCommandClient
from telescreen.integrations.notification_delivery import NotificationCommandDeliveryHandler
from telescreen.integrations.notification_outbox import NotificationOutbox
from telescreen.inventory_service.transport import NOTIFICATION_PREFIX, ServiceHttp
from telescreen.ports.directory_projection import (
    DirectoryEntity,
    DirectoryEntityKind,
    MemberEntity,
)
from telescreen.ports.notification_commands import (
    DirectoryOperation,
    NotificationCommand,
)
from telescreen.ports.workflow import ClaimedTask, TaskResult, TaskResultKind
from tests.support.db import SqliteHarness, make_sqlite_harness

_NOW: datetime = datetime(2026, 10, 16, tzinfo=UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "body", "expected", "acknowledged"),
    (
        (200, '"applied"', TaskResultKind.DONE, True),
        (200, '"deferred"', TaskResultKind.RETRY, False),
        (500, '{"error":"failed"}', TaskResultKind.RETRY, False),
        (200, '"bad-receipt"', TaskResultKind.RETRY, False),
    ),
)
async def test_command_delivery_acknowledges_only_applied_receipts(
    status_code: int, body: str, expected: TaskResultKind, acknowledged: bool
) -> None:
    """Keep durable commands pending for deferred, HTTP and malformed outcomes."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(_NOW)
    outbox: NotificationOutbox = NotificationOutbox()

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"{NOTIFICATION_PREFIX}/commands"
        return httpx.Response(status_code, content=body)

    http: httpx.AsyncClient = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    transport: ServiceHttp = ServiceHttp(
        http, "http://notification.test", SecretValue("service-token"), NOTIFICATION_PREFIX
    )
    receiver: NotificationCommandClient = NotificationCommandClient(transport)
    handler: NotificationCommandDeliveryHandler = NotificationCommandDeliveryHandler(
        harness.sessions, outbox, receiver, clock
    )
    command: NotificationCommand = NotificationCommand(
        "event-1",
        _NOW,
        (
            DirectoryOperation(
                DirectoryEntity(
                    DirectoryEntityKind.MEMBER,
                    "member-1",
                    1,
                    True,
                    MemberEntity("member-1", "alice", "Alice", True, None, None, None),
                )
            ),
        ),
    )
    task: ClaimedTask = ClaimedTask(
        "task-1",
        EngineTaskKind.NOTIFICATION_COMMAND_DELIVERY,
        command.event_id,
        json_null(),
        1,
        5,
        "notification-command:event-1",
    )
    try:
        async with harness.sessions.begin() as session:
            await outbox.append(session, command)
        result: TaskResult = await handler.handle(task)
        assert result.kind is expected
        async with harness.sessions() as session:
            row: NotificationOutboxRow = await session.get_one(
                NotificationOutboxRow, command.event_id
            )
            assert (row.delivered_at is not None) is acknowledged
    finally:
        await http.aclose()
        await harness.close()
