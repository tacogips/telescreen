"""Safe integration configuration and last-check status."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.config import TelescreenSettings
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode, IntegrationKind
from telescreen.integrations.repository import IntegrationCheck, IntegrationCheckRepository
from telescreen.ports.notification_commands import ChannelStatus


class ChannelStatusSource(Protocol):
    """Read configured delivery channels from the notification owner."""

    async def status(self) -> ChannelStatus:
        """Return only configured channel flags."""
        ...


@dataclass(frozen=True)
class IntegrationStatus:
    """Public status containing no connection details or credentials."""

    kind: IntegrationKind
    configured: bool
    last_check_at: datetime | None
    last_check_ok: bool | None
    error_code: ErrorCode | None
    docs_path: str


@dataclass(frozen=True)
class IntegrationStatusService:
    """Report configured flags and safe results for supported integrations."""

    settings: TelescreenSettings
    checks: IntegrationCheckRepository
    channels: ChannelStatusSource | None = None

    async def list(self, session: AsyncSession) -> tuple[IntegrationStatus, ...]:
        """Return status for each supported integration, without secret values."""
        latest: tuple[IntegrationCheck, ...] = await self.checks.get_all(session)
        configured: ChannelStatus | None = None
        if self.channels is not None:
            try:
                configured = await self.channels.status()
            except TelescreenError:
                configured = ChannelStatus(False, False)
        return tuple(
            IntegrationStatus(
                kind,
                self._configured(kind, configured),
                self._at(kind, latest),
                self._ok(kind, latest),
                self._error(kind, latest),
                f"/docs#integrations-{kind.value}",
            )
            for kind in IntegrationKind
        )

    def _configured(self, kind: IntegrationKind, channels: ChannelStatus | None) -> bool:
        """Determine configuration only from presence of non-secret settings."""
        if kind is IntegrationKind.SLACK:
            if channels is not None:
                return channels.slack
            return bool(self.settings.slack_bot_token_ref)
        if kind is IntegrationKind.EMAIL:
            if channels is not None:
                return channels.email
            return bool(self.settings.smtp_host and self.settings.smtp_from)
        if kind is IntegrationKind.GITHUB_ISSUES:
            return bool(self.settings.github_issues_token_ref and self.settings.github_issues_repos)
        return bool(self.settings.oidc and self.settings.oidc.issuer)

    def _at(self, kind: IntegrationKind, latest: tuple[IntegrationCheck, ...]) -> datetime | None:
        """Find last-check timestamp for an integration kind."""
        item: IntegrationCheck | None = next(
            (entry for entry in latest if entry.kind is kind), None
        )
        return item.checked_at if item is not None else None

    def _ok(self, kind: IntegrationKind, latest: tuple[IntegrationCheck, ...]) -> bool | None:
        """Find last-check success flag for an integration kind."""
        item: IntegrationCheck | None = next(
            (entry for entry in latest if entry.kind is kind), None
        )
        return item.ok if item is not None else None

    def _error(
        self, kind: IntegrationKind, latest: tuple[IntegrationCheck, ...]
    ) -> ErrorCode | None:
        """Find last safe error code for an integration kind."""
        item: IntegrationCheck | None = next(
            (entry for entry in latest if entry.kind is kind), None
        )
        return item.error_code if item is not None else None
