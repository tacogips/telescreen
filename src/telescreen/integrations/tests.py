"""Run fixed, safe connectivity checks for configured integrations."""

from dataclasses import dataclass, field
from datetime import datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.config import TelescreenSettings
from telescreen.core.errors import TelescreenError
from telescreen.core.json import json_bool, json_object, json_str
from telescreen.core.secrets import EnvSource, resolve_secret
from telescreen.core.vocabulary import ActorKind, ErrorCode, IntegrationKind, NotificationChannel
from telescreen.integrations.repository import IntegrationCheckRepository
from telescreen.integrations.status import ChannelStatusSource
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.identity import BearerVerifier
from telescreen.ports.notification_commands import ChannelStatus
from telescreen.ports.notifications import NotificationTester, TestOutcome


@dataclass(frozen=True)
class IntegrationCheckResult:
    """Safe result returned by an integration test action."""

    kind: IntegrationKind
    checked_at: datetime
    ok: bool
    error_code: ErrorCode | None


@dataclass(frozen=True)
class IntegrationTester:
    """Test configured integrations without returning provider diagnostics."""

    tester: NotificationTester
    verifier: BearerVerifier | None
    github_transport: httpx.AsyncBaseTransport | None
    settings: TelescreenSettings
    env: EnvSource = field(repr=False)
    checks: IntegrationCheckRepository
    audit: AuditLog
    clock: Clock
    channels: ChannelStatusSource | None = None

    async def run(
        self, kind: IntegrationKind, member_id: str, session: AsyncSession
    ) -> IntegrationCheckResult:
        """Execute one check and persist only its timestamp, result and enum code."""
        if not await self._configured(kind):
            raise TelescreenError(
                ErrorCode.INTEGRATION_UNCONFIGURED, "integration is not configured"
            )
        now: datetime = ensure_utc(self.clock.now())
        ok: bool = False
        error_code: ErrorCode | None = None
        try:
            if kind is IntegrationKind.SLACK:
                outcome: TestOutcome = await self.tester.send_test(
                    NotificationChannel.SLACK, member_id
                )
                ok, error_code = outcome.ok, outcome.error_code
            elif kind is IntegrationKind.EMAIL:
                outcome = await self.tester.send_test(NotificationChannel.EMAIL, member_id)
                ok, error_code = outcome.ok, outcome.error_code
            elif kind is IntegrationKind.GITHUB_ISSUES:
                ok = await self._github_test()
                error_code = None if ok else ErrorCode.PROVIDER_ERROR
            elif kind is IntegrationKind.OIDC:
                if self.verifier is None:
                    ok, error_code = False, ErrorCode.IDP_UNAVAILABLE
                else:
                    await self.verifier.metadata()
                    ok = True
        except TelescreenError as error:
            ok, error_code = False, error.code
        except (httpx.HTTPError, OSError, ValueError):
            ok, error_code = False, ErrorCode.PROVIDER_ERROR
        await self.checks.upsert(session, kind, now, ok, error_code)
        await self.audit.append(
            session,
            AuditEntry(
                ActorKind.MEMBER,
                f"member:{member_id}",
                "integration.tested",
                "integration",
                kind.value,
                "Integration test completed",
                json_object((("kind", json_str(kind.value)), ("ok", json_bool(ok)))),
            ),
        )
        return IntegrationCheckResult(kind, now, ok, error_code)

    async def _configured(self, kind: IntegrationKind) -> bool:
        """Check required settings by presence without resolving secret refs."""
        if kind in (IntegrationKind.SLACK, IntegrationKind.EMAIL) and self.channels is not None:
            try:
                channels: ChannelStatus = await self.channels.status()
            except TelescreenError:
                return False
            return channels.slack if kind is IntegrationKind.SLACK else channels.email
        if kind is IntegrationKind.SLACK:
            return bool(self.settings.slack_bot_token_ref)
        if kind is IntegrationKind.EMAIL:
            return bool(self.settings.smtp_host and self.settings.smtp_from)
        if kind is IntegrationKind.GITHUB_ISSUES:
            return bool(self.settings.github_issues_token_ref and self.settings.github_issues_repos)
        return bool(self.settings.oidc and self.settings.oidc.issuer)

    async def _github_test(self) -> bool:
        """GET every allow-listed repository with the dedicated token and fixed timeout."""
        token_ref: str | None = self.settings.github_issues_token_ref
        if token_ref is None:
            return False
        token: str = resolve_secret(token_ref, self.env).value
        base_url: str = self.settings.github_api_url.rstrip("/")
        async with httpx.AsyncClient(transport=self.github_transport, timeout=5.0) as client:
            for repo in self.settings.github_issues_repos:
                response: httpx.Response = await client.get(
                    f"{base_url}/repos/{repo}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                    },
                )
                if response.status_code != 200:
                    return False
        return True
