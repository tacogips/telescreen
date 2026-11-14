"""Integration status, test outcomes and response masking."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import FakeClock
from telescreen.core.config import TelescreenSettings
from telescreen.core.errors import TelescreenError
from telescreen.core.oidc_settings import OidcSettings
from telescreen.core.secrets import EnvSource, SecretValue
from telescreen.core.vocabulary import (
    ErrorCode,
    IntegrationKind,
    NotificationChannel,
    OidcProvisioning,
    Role,
)
from telescreen.db.models import IntegrationCheckRow
from telescreen.integrations.notification_client import (
    ChannelStatusClient,
    RemoteNotificationTester,
)
from telescreen.integrations.repository import IntegrationCheckRepository
from telescreen.integrations.status import (
    ChannelStatusSource,
    IntegrationStatus,
    IntegrationStatusService,
)
from telescreen.integrations.tests import IntegrationCheckResult, IntegrationTester
from telescreen.inventory_service.transport import NOTIFICATION_PREFIX, ServiceHttp
from telescreen.ports.audit import AuditEntry
from telescreen.ports.identity import ProviderMetadata, VerifiedToken
from telescreen.ports.notification_commands import ChannelStatus
from telescreen.ports.notifications import TestOutcome as NotificationTestOutcome
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass(frozen=True)
class StubEnv(EnvSource):
    """Small read-only environment adapter for tests."""

    values: tuple[tuple[str, str], ...]

    def get(self, key: str, /) -> str | None:
        """Read one preconfigured value."""
        return next((value for name, value in self.values if name == key), None)


@dataclass
class StubChannelTester:
    """Record channel tests without sending network requests."""

    channels: list[NotificationChannel] = field(default_factory=list)

    async def send_test(
        self, channel: NotificationChannel, member_id: str
    ) -> NotificationTestOutcome:
        """Return a safe successful test outcome."""
        del member_id
        self.channels.append(channel)
        return NotificationTestOutcome(True, None, "notification-id")


@dataclass(frozen=True)
class StubChannelStatusSource(ChannelStatusSource):
    """Return safe Notification-owned channel configuration."""

    value: ChannelStatus

    async def status(self) -> ChannelStatus:
        """Return the configured channel flags."""
        return self.value


@dataclass(frozen=True)
class UnavailableChannelStatusSource(ChannelStatusSource):
    """Represent an unreachable Notification service without provider details."""

    async def status(self) -> ChannelStatus:
        """Raise the stable service error used by the HTTP client."""
        raise TelescreenError(ErrorCode.SERVICE_UNAVAILABLE, "service unavailable")


@dataclass
class RecordingAuditLog:
    """Collect audit entries and exclude database persistence from assertions."""

    entries: list[AuditEntry] = field(default_factory=list)

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Record one safe integration audit event."""
        del session
        self.entries.append(entry)
        return len(self.entries)


@dataclass(frozen=True)
class UnavailableVerifier:
    """Verifier fake that returns only a stable IdP outage code."""

    async def verify(self, token: str) -> VerifiedToken:
        """Reject the unused verification path with a static error."""
        del token
        raise TelescreenError(ErrorCode.IDP_UNAVAILABLE, "identity provider unavailable")

    async def metadata(self) -> ProviderMetadata:
        """Simulate provider discovery failure without returning diagnostics."""
        raise TelescreenError(ErrorCode.IDP_UNAVAILABLE, "identity provider unavailable")


def oidc_settings() -> OidcSettings:
    """Build minimal OIDC configuration for integration testing."""
    return OidcSettings(
        issuer="https://identity.example.invalid",
        client_id="client",
        audience="audience",
        scopes=("openid",),
        algorithms=("RS256",),
        leeway_seconds=0,
        jwks_cache_seconds=60,
        role_claim="role",
        role_map=(),
        default_role=Role.VIEWER,
        group_claim="groups",
        team_map=(),
        provisioning=OidcProvisioning.LINK,
    )


@pytest.mark.asyncio
async def test_status_excludes_configured_connection_values() -> None:
    """Status exposes only configuration flags and safe metadata."""
    harness: SqliteHarness = await make_sqlite_harness()
    settings: TelescreenSettings = TelescreenSettings(
        slack_bot_token_ref="env:BOT_TOKEN_SECRET_REF",
        smtp_host="mail-host-private.invalid",
        smtp_from="private-sender@example.invalid",
        github_issues_token_ref="env:GITHUB_TOKEN_SECRET_REF",
        github_issues_repos=("owner/repo",),
    )
    async with harness.sessions() as session:
        items: tuple[IntegrationStatus, ...] = await IntegrationStatusService(
            settings, IntegrationCheckRepository()
        ).list(session)
    rendered: str = repr(items)
    for private_value in (
        "env:BOT_TOKEN_SECRET_REF",
        "mail-host-private.invalid",
        "private-sender@example.invalid",
        "env:GITHUB_TOKEN_SECRET_REF",
    ):
        assert private_value not in rendered
    assert tuple(item.configured for item in items) == (True, True, True, False)
    await harness.close()


@pytest.mark.asyncio
async def test_slack_check_persists_safe_result_and_audit() -> None:
    """A successful channel test stores the boolean and enum kind only."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 12, tzinfo=UTC))
    audit: RecordingAuditLog = RecordingAuditLog()
    tester: IntegrationTester = IntegrationTester(
        StubChannelTester(),
        None,
        None,
        TelescreenSettings(slack_bot_token_ref="env:SLACK_TOKEN"),
        StubEnv(()),
        IntegrationCheckRepository(),
        audit,
        clock,
    )
    async with harness.sessions.begin() as session:
        result: IntegrationCheckResult = await tester.run(
            IntegrationKind.SLACK, "member-1", session
        )
    async with harness.sessions() as session:
        check: IntegrationCheckRow | None = await session.get(IntegrationCheckRow, "slack")
    assert result.ok is True
    assert check is not None and check.ok is True
    assert len(audit.entries) == 1
    assert audit.entries[0].action == "integration.tested"
    assert audit.entries[0].details.member_names() == ("kind", "ok")
    await harness.close()


@pytest.mark.asyncio
async def test_remote_channel_flags_drive_status_and_test_configuration() -> None:
    """Use Notification-owned configuration instead of local SMTP/Slack references."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 12, tzinfo=UTC))
    settings: TelescreenSettings = TelescreenSettings(
        slack_bot_token_ref="env:unused-slack-token",
        smtp_host="unused-mail-host",
        smtp_from="unused-sender@example.invalid",
    )
    channels: StubChannelStatusSource = StubChannelStatusSource(ChannelStatus(False, True))
    tester: IntegrationTester = IntegrationTester(
        StubChannelTester(),
        None,
        None,
        settings,
        StubEnv(()),
        IntegrationCheckRepository(),
        RecordingAuditLog(),
        clock,
        channels=channels,
    )
    async with harness.sessions() as session:
        status: tuple[IntegrationStatus, ...] = await IntegrationStatusService(
            settings, IntegrationCheckRepository(), channels
        ).list(session)
    assert tuple(item.configured for item in status) == (True, False, False, False)
    async with harness.sessions.begin() as session:
        success: IntegrationCheckResult = await tester.run(
            IntegrationKind.SLACK, "member-1", session
        )
        with pytest.raises(TelescreenError) as raised:
            await tester.run(IntegrationKind.EMAIL, "member-1", session)
    assert success.ok is True
    assert raised.value.code is ErrorCode.INTEGRATION_UNCONFIGURED
    await harness.close()


@pytest.mark.asyncio
async def test_unavailable_notification_channel_status_fails_closed() -> None:
    """An unreachable Notification service reports both delivery channels unavailable."""
    harness: SqliteHarness = await make_sqlite_harness()
    settings: TelescreenSettings = TelescreenSettings(
        slack_bot_token_ref="env:unused-slack-token",
        smtp_host="unused-mail-host",
        smtp_from="unused-sender@example.invalid",
    )
    async with harness.sessions() as session:
        status: tuple[IntegrationStatus, ...] = await IntegrationStatusService(
            settings,
            IntegrationCheckRepository(),
            UnavailableChannelStatusSource(),
        ).list(session)
    assert status[0].configured is False
    assert status[1].configured is False
    await harness.close()


@pytest.mark.asyncio
async def test_remote_notification_tester_runs_email_integration_check() -> None:
    """Exercise split email configuration and test-send over the typed HTTP clients."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 12, tzinfo=UTC))
    requests: list[httpx.Request] = []

    def reply(request: httpx.Request) -> httpx.Response:
        """Return only the two versioned Notification service contracts."""
        requests.append(request)
        if request.method == "GET" and request.url.path == f"{NOTIFICATION_PREFIX}/channels":
            return httpx.Response(200, json={"email": True, "slack": False})
        if request.method == "POST" and request.url.path == f"{NOTIFICATION_PREFIX}/test":
            return httpx.Response(
                200,
                json={"ok": True, "error_code": None, "notification_id": "safe-test-id"},
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
        transport: ServiceHttp = ServiceHttp(
            http, "http://notification", SecretValue("service-token"), NOTIFICATION_PREFIX
        )
        remote: RemoteNotificationTester = RemoteNotificationTester(transport)
        channels: ChannelStatusClient = ChannelStatusClient(transport)
        tester: IntegrationTester = IntegrationTester(
            remote,
            None,
            None,
            TelescreenSettings(),
            StubEnv(()),
            IntegrationCheckRepository(),
            RecordingAuditLog(),
            clock,
            channels=channels,
        )
        async with harness.sessions.begin() as session:
            result: IntegrationCheckResult = await tester.run(
                IntegrationKind.EMAIL, "member-1", session
            )
    assert result.ok is True
    assert result.error_code is None
    assert tuple((request.method, request.url.path) for request in requests) == (
        ("GET", f"{NOTIFICATION_PREFIX}/channels"),
        ("POST", f"{NOTIFICATION_PREFIX}/test"),
    )
    assert all(
        request.headers["x-telescreen-service-token"] == "service-token" for request in requests
    )
    await harness.close()


@pytest.mark.asyncio
async def test_github_test_uses_allow_list_and_unconfigured_is_rejected() -> None:
    """GitHub checks only configured repositories and unconfigured tests fail safely."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 12, tzinfo=UTC))
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Record safe paths and answer repository checks."""
        requested.append(request.url.path)
        return httpx.Response(200)

    tester: IntegrationTester = IntegrationTester(
        StubChannelTester(),
        None,
        httpx.MockTransport(handler),
        TelescreenSettings(
            github_api_url="https://api.github.test",
            github_issues_token_ref="env:GH_TOKEN",
            github_issues_repos=("owner/one", "owner/two"),
        ),
        StubEnv((("GH_TOKEN", "configured-token-private"),)),
        IntegrationCheckRepository(),
        RecordingAuditLog(),
        clock,
    )
    async with harness.sessions.begin() as session:
        result: IntegrationCheckResult = await tester.run(
            IntegrationKind.GITHUB_ISSUES, "member-1", session
        )
    assert result.ok is True
    assert requested == ["/repos/owner/one", "/repos/owner/two"]
    failing_tester: IntegrationTester = IntegrationTester(
        StubChannelTester(),
        None,
        httpx.MockTransport(
            lambda request: httpx.Response(404 if request.url.path.endswith("owner/two") else 200)
        ),
        TelescreenSettings(
            github_api_url="https://api.github.test",
            github_issues_token_ref="env:GH_TOKEN",
            github_issues_repos=("owner/one", "owner/two"),
        ),
        StubEnv((("GH_TOKEN", "configured-token-private"),)),
        IntegrationCheckRepository(),
        RecordingAuditLog(),
        clock,
    )
    async with harness.sessions.begin() as session:
        failed_result: IntegrationCheckResult = await failing_tester.run(
            IntegrationKind.GITHUB_ISSUES, "member-1", session
        )
    assert failed_result.ok is False
    assert failed_result.error_code is ErrorCode.PROVIDER_ERROR
    oidc_tester: IntegrationTester = IntegrationTester(
        StubChannelTester(),
        UnavailableVerifier(),
        None,
        TelescreenSettings(oidc=oidc_settings()),
        StubEnv(()),
        IntegrationCheckRepository(),
        RecordingAuditLog(),
        clock,
    )
    async with harness.sessions.begin() as session:
        oidc_result: IntegrationCheckResult = await oidc_tester.run(
            IntegrationKind.OIDC, "member-1", session
        )
    assert oidc_result.ok is False
    assert oidc_result.error_code is ErrorCode.IDP_UNAVAILABLE
    unconfigured: IntegrationTester = IntegrationTester(
        StubChannelTester(),
        None,
        None,
        TelescreenSettings(),
        StubEnv(()),
        IntegrationCheckRepository(),
        RecordingAuditLog(),
        clock,
    )
    async with harness.sessions() as session:
        with pytest.raises(TelescreenError) as raised:
            await unconfigured.run(IntegrationKind.GITHUB_ISSUES, "member-1", session)
    assert raised.value.code is ErrorCode.INTEGRATION_UNCONFIGURED
    await harness.close()
