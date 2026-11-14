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
from telescreen.core.secrets import EnvSource
from telescreen.core.vocabulary import (
    ErrorCode,
    IntegrationKind,
    NotificationChannel,
    OidcProvisioning,
    Role,
)
from telescreen.db.models import IntegrationCheckRow
from telescreen.integrations.repository import IntegrationCheckRepository
from telescreen.integrations.status import IntegrationStatus, IntegrationStatusService
from telescreen.integrations.tests import IntegrationCheckResult, IntegrationTester
from telescreen.ports.audit import AuditEntry
from telescreen.ports.identity import ProviderMetadata, VerifiedToken
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
