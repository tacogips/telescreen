"""Service credentials and producer operation authorization for Notification."""

from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from telescreen.core.clock import FakeClock
from telescreen.core.config import TelescreenSettings
from telescreen.core.errors import TelescreenError
from telescreen.core.secrets import EnvSource
from telescreen.core.vocabulary import (
    DlpPolicy,
    ErrorCode,
    NotificationCategory,
    NotificationChannel,
    NotificationEvent,
)
from telescreen.db.models import (
    NotificationDirectoryEntryRow,
    NotificationInboxRow,
    NotificationRow,
)
from telescreen.db.ownership import Store
from telescreen.notification_service.app import (
    NOTIFICATION_PREFIX,
    SERVICE_HEADER,
    NotificationRuntime,
    create_notification_app,
)
from telescreen.notification_service.inbox import Producer
from telescreen.notification_service.wiring import build_notification_runtime
from telescreen.ports.directory_projection import (
    DirectoryEntity,
    DirectoryEntityKind,
    MemberEntity,
)
from telescreen.ports.notification_commands import (
    DirectoryOperation,
    MemberSendOperation,
    NotificationCommand,
    NotificationCommandReceipt,
    NotificationOperation,
    ProducerRedactionHits,
    SendOperation,
    encode_notification_command,
)
from telescreen.ports.notifications import NotificationContext
from tests.support.db import SqliteHarness, make_sqlite_harness


class _Environment(EnvSource):
    """Provide distinct non-secret test credentials by reference name."""

    def get(self, key: str, /) -> str | None:
        """Resolve the two service token references used by the test runtime."""
        return {"TS": "telescreen-test-token", "INV": "inventory-test-token"}.get(key)


async def _runtime(
    harness: SqliteHarness,
) -> tuple[NotificationRuntime, httpx.AsyncClient, FakeClock]:
    """Build production Notification adapters over an isolated Notification store."""
    clock: FakeClock = FakeClock(datetime(2026, 10, 16, tzinfo=UTC))
    service_http: httpx.AsyncClient = httpx.AsyncClient()
    runtime: NotificationRuntime = build_notification_runtime(
        TelescreenSettings(
            notification_database_url="sqlite+aiosqlite:///:memory:",
            notification_service_token_ref="env:TS",
            notification_inventory_token_ref="env:INV",
            dlp_policy=DlpPolicy.MASK,
        ),
        _Environment(),
        service_http,
        harness.sessions,
        clock,
    )
    return runtime, service_http, clock


def _directory_operation() -> DirectoryOperation:
    """Return a revisioned member entity for producer-boundary assertions."""
    entity: DirectoryEntity = DirectoryEntity(
        DirectoryEntityKind.MEMBER,
        "member-1",
        1,
        True,
        MemberEntity("member-1", "alice", "Alice", True, "admin", "alice@example.test", None),
    )
    return DirectoryOperation(entity)


def _send_operation(event: NotificationEvent | None) -> SendOperation:
    """Return a direct send with the requested event kind."""
    return SendOperation(
        NotificationChannel.EMAIL,
        ("member:alice",),
        "subject",
        "body",
        "system:test",
        None,
        None,
        event,
        None,
        None,
        False,
        None,
        None,
        True,
        None,
        True,
        ProducerRedactionHits(0, 0),
    )


async def test_inventory_service_credential_is_scoped_to_post_commands() -> None:
    """Reject missing or out-of-scope credentials and allow Telescreen internal routes."""
    harness: SqliteHarness = await make_sqlite_harness(store=Store.NOTIFICATION)
    runtime: NotificationRuntime
    service_http: httpx.AsyncClient
    clock: FakeClock
    runtime, service_http, clock = await _runtime(harness)
    del clock
    app: FastAPI = create_notification_app(runtime)
    transport: httpx.ASGITransport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://notification.test"
        ) as client:
            missing: httpx.Response = await client.get(NOTIFICATION_PREFIX + "/channels")
            wrong: httpx.Response = await client.get(
                NOTIFICATION_PREFIX + "/channels",
                headers={SERVICE_HEADER: "wrong-test-token"},
            )
            assert missing.status_code == wrong.status_code == 401
            assert missing.json()["error"]["code"] == "unauthorized"
            assert wrong.json()["error"]["code"] == "unauthorized"

            denied: tuple[httpx.Response, ...] = (
                await client.get(
                    NOTIFICATION_PREFIX + "/channels",
                    headers={SERVICE_HEADER: "inventory-test-token"},
                ),
                await client.get(
                    NOTIFICATION_PREFIX + "/health",
                    headers={SERVICE_HEADER: "inventory-test-token"},
                ),
                await client.post(
                    NOTIFICATION_PREFIX + "/test",
                    headers={SERVICE_HEADER: "inventory-test-token"},
                    json={"channel": "email", "member_id": "member-1"},
                ),
                await client.post(
                    NOTIFICATION_PREFIX + "/templates/lookup",
                    headers={SERVICE_HEADER: "inventory-test-token"},
                    json="alerts",
                ),
                await client.get(
                    NOTIFICATION_PREFIX + "/commands",
                    headers={SERVICE_HEADER: "inventory-test-token"},
                ),
            )
            assert all(response.status_code == 403 for response in denied)
            assert all(response.json()["error"]["code"] == "forbidden" for response in denied)

            telescreen: httpx.Response = await client.get(
                NOTIFICATION_PREFIX + "/channels",
                headers={SERVICE_HEADER: "telescreen-test-token"},
            )
            assert telescreen.status_code == 200
            assert telescreen.json() == {"email": False, "slack": False}

            command: NotificationCommand = NotificationCommand(
                "event-inventory-directory",
                runtime.context.clock.now(),
                (_directory_operation(),),
            )
            directory_response: httpx.Response = await client.post(
                NOTIFICATION_PREFIX + "/commands",
                headers={SERVICE_HEADER: "inventory-test-token"},
                content=encode_notification_command(command),
            )
            assert directory_response.status_code == 403
            assert directory_response.json()["error"]["code"] == "forbidden"
        async with harness.sessions() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(NotificationDirectoryEntryRow)
                )
                or 0
            ) == 0
            assert (
                await session.scalar(select(func.count()).select_from(NotificationInboxRow)) or 0
            ) == 0
            assert (
                await session.scalar(select(func.count()).select_from(NotificationRow)) or 0
            ) == 0
    finally:
        await service_http.aclose()
        await harness.close()


async def test_inventory_inbox_rejects_forbidden_operation_kinds_without_writes() -> None:
    """Enforce producer limits before writes while allowing Telescreen directory updates."""
    harness: SqliteHarness = await make_sqlite_harness(store=Store.NOTIFICATION)
    runtime: NotificationRuntime
    service_http: httpx.AsyncClient
    clock: FakeClock
    runtime, service_http, clock = await _runtime(harness)
    operations: tuple[NotificationOperation, ...] = (
        _directory_operation(),
        _send_operation(None),
        _send_operation(NotificationEvent.ISSUE_ASSIGNED),
        MemberSendOperation(
            "member-1",
            NotificationCategory.ALERTS,
            NotificationEvent.ISSUE_ASSIGNED,
            NotificationContext(),
            None,
            ProducerRedactionHits(0, 0),
        ),
    )
    try:
        for index, operation in enumerate(operations):
            command: NotificationCommand = NotificationCommand(
                f"event-forbidden-{index}", clock.now(), (operation,)
            )
            with pytest.raises(TelescreenError) as raised:
                await runtime.inbox.receive(command, Producer.INVENTORY)
            assert raised.value.code is ErrorCode.FORBIDDEN
            async with harness.sessions() as session:
                assert (
                    await session.scalar(select(func.count()).select_from(NotificationInboxRow))
                    or 0
                ) == 0
                assert (
                    await session.scalar(select(func.count()).select_from(NotificationRow)) or 0
                ) == 0
                assert (
                    await session.scalar(
                        select(func.count()).select_from(NotificationDirectoryEntryRow)
                    )
                    or 0
                ) == 0

        directory_command: NotificationCommand = NotificationCommand(
            "event-telescreen-directory", clock.now(), (_directory_operation(),)
        )
        applied: NotificationCommandReceipt = await runtime.inbox.receive(
            directory_command, Producer.TELESCREEN
        )
        assert applied is NotificationCommandReceipt.APPLIED
        async with harness.sessions() as session:
            replicated: DirectoryEntity | None = await runtime.replica.get(
                session, DirectoryEntityKind.MEMBER, "member-1"
            )
        assert replicated is not None
    finally:
        await service_http.aclose()
        await harness.close()
