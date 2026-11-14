"""Slack chat.postMessage adapter for sealed notification content."""

from dataclasses import dataclass

import httpx
from pydantic import TypeAdapter, ValidationError

from telescreen.core.errors import TelescreenError
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import ErrorCode
from telescreen.notifications.dlp import CensoredMessage
from telescreen.ports.collectors import RateLimited


@dataclass(frozen=True)
class SlackResponse:
    """Minimal typed response expected from Slack's chat.postMessage endpoint."""

    ok: bool
    error: str | None = None
    channel: str | None = None
    ts: str | None = None


@dataclass(frozen=True)
class SlackPostResult:
    """Channel id and message timestamp reported by a successful post."""

    channel: str
    ts: str


@dataclass(frozen=True)
class SlackNotifier:
    """Send a sealed notification to a Slack channel or user."""

    http: httpx.AsyncClient
    bot_token: SecretValue | None
    api_base: str = "https://slack.com/api"

    async def send(self, target: str, message: CensoredMessage) -> SlackPostResult:
        """Post masked content and convert provider failures to safe task outcomes."""
        if self.bot_token is None:
            raise TelescreenError(ErrorCode.CONFIG_ERROR, "Slack bot token is not configured")
        response: httpx.Response = await self.http.post(
            f"{self.api_base.rstrip('/')}/chat.postMessage",
            headers={"Authorization": f"Bearer {self.bot_token.value}"},
            json={"channel": target, "text": f"{message.subject}\n{message.body}"},
        )
        if response.status_code == 429:
            retry_after: float | None = _retry_after(response)
            raise RateLimited(retry_after)
        if response.is_error:
            raise TelescreenError(ErrorCode.DELIVERY_FAILED, "Slack delivery failed")
        try:
            result: SlackResponse = TypeAdapter(SlackResponse).validate_json(response.content)
        except (ValidationError, ValueError):
            raise TelescreenError(ErrorCode.DELIVERY_FAILED, "Slack delivery failed") from None
        if not result.ok:
            raise TelescreenError(ErrorCode.DELIVERY_FAILED, "Slack delivery failed")
        return SlackPostResult(result.channel or target, result.ts or "")


def _retry_after(response: httpx.Response) -> float | None:
    """Parse a provider retry delay without exposing response content."""
    value: str | None = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        seconds: float = float(value)
    except ValueError:
        return None
    return max(0.0, seconds)
