"""Typed Slack Web API client backed by httpx."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import ParseResult, urlparse

import httpx
from pydantic import TypeAdapter, ValidationError

from telescreen.core.errors import TelescreenError
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import ErrorCode
from telescreen.ports.collectors import RateLimited, ResourceGoneError


@dataclass(frozen=True)
class SlackMetadata:
    """Pagination metadata returned by Slack."""

    next_cursor: str = ""


@dataclass(frozen=True)
class SlackMessage:
    """Message fields consumed by the collector."""

    ts: str
    text: str = field(repr=False)


@dataclass(frozen=True)
class SlackChannel:
    """Conversation identifier returned by Slack."""

    id: str


@dataclass(frozen=True)
class SlackFile:
    """File fields consumed by the collector."""

    id: str
    name: str = field(repr=False)
    mimetype: str
    size: int
    url_private: str = field(repr=False)
    timestamp: int | None = None


@dataclass(frozen=True)
class HistoryResponse:
    """Validated Slack history response."""

    ok: bool
    messages: list[SlackMessage]
    response_metadata: SlackMetadata = SlackMetadata()
    error: str | None = None


@dataclass(frozen=True)
class ChannelListResponse:
    """Validated Slack conversation list response."""

    ok: bool
    channels: list[SlackChannel]
    response_metadata: SlackMetadata = SlackMetadata()
    error: str | None = None


@dataclass(frozen=True)
class FileListResponse:
    """Validated Slack file list response."""

    ok: bool
    files: list[SlackFile]
    response_metadata: SlackMetadata = SlackMetadata()
    error: str | None = None


@dataclass(frozen=True)
class FileInfoResponse:
    """Validated Slack file detail response."""

    ok: bool
    file: SlackFile
    error: str | None = None


@dataclass(frozen=True)
class AuthResponse:
    """Validated Slack authentication identity response."""

    ok: bool
    team_id: str
    error: str | None = None


@dataclass(frozen=True)
class SlackStatus:
    """Common status fields returned by Slack API responses."""

    ok: bool
    error: str | None = None


@dataclass
class SlackApi:
    """Call the small Slack Web API surface needed by the adapter."""

    http: httpx.AsyncClient
    token: SecretValue = field(repr=False)
    api_base: str = "https://slack.com/api"
    _team_id: str | None = field(default=None, init=False, repr=False)

    async def conversations_history(
        self,
        channel: str,
        oldest: str | None,
        latest: str | None,
        cursor: str | None,
        limit: int,
        inclusive: bool = False,
    ) -> HistoryResponse:
        """Fetch one page of conversation history."""
        params: tuple[tuple[str, str | None], ...] = (
            ("channel", channel),
            ("oldest", oldest),
            ("latest", latest),
            ("cursor", cursor),
            ("limit", str(limit)),
            ("inclusive", "true" if inclusive else None),
        )
        return await self._call(
            "conversations.history",
            params,
            TypeAdapter(HistoryResponse),
        )

    async def conversations_replies(
        self,
        channel: str,
        thread_ts: str,
        oldest: str | None,
        latest: str | None,
        cursor: str | None,
        limit: int,
        inclusive: bool = False,
    ) -> HistoryResponse:
        """Fetch one page of replies for a conversation thread."""
        params: tuple[tuple[str, str | None], ...] = (
            ("channel", channel),
            ("ts", thread_ts),
            ("oldest", oldest),
            ("latest", latest),
            ("cursor", cursor),
            ("limit", str(limit)),
            ("inclusive", "true" if inclusive else None),
        )
        return await self._call(
            "conversations.replies",
            params,
            TypeAdapter(HistoryResponse),
        )

    async def conversations_list(self, cursor: str | None, types: str) -> ChannelListResponse:
        """Fetch one page of conversations."""
        return await self._call(
            "conversations.list",
            (("cursor", cursor), ("types", types), ("limit", "200")),
            TypeAdapter(ChannelListResponse),
        )

    async def files_list(
        self, channel: str, ts_from: str | None, cursor: str | None
    ) -> FileListResponse:
        """Fetch one page of files shared in a conversation."""
        return await self._call(
            "files.list",
            (("channel", channel), ("ts_from", ts_from), ("cursor", cursor), ("count", "200")),
            TypeAdapter(FileListResponse),
        )

    async def files_info(self, file_id: str) -> SlackFile:
        """Fetch the current metadata for one file."""
        response: FileInfoResponse = await self._call(
            "files.info", (("file", file_id),), TypeAdapter(FileInfoResponse)
        )
        return response.file

    async def team_id(self) -> str:
        """Return and cache the workspace id authenticated by this API client."""
        if self._team_id is None:
            response: AuthResponse = await self._call("auth.test", (), TypeAdapter(AuthResponse))
            self._team_id = response.team_id
        return self._team_id

    async def download(self, url_private: str, max_bytes: int) -> bytes:
        """Download at most max_bytes plus one sentinel byte without logging content."""
        parsed_url: ParseResult = urlparse(url_private)
        if parsed_url.scheme != "https" or parsed_url.hostname != "files.slack.com":
            raise TelescreenError(ErrorCode.FETCH_FAILED, "Slack file URL was invalid")
        if max_bytes < 0:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "Slack file size limit was invalid")
        try:
            async with self.http.stream(
                "GET",
                url_private,
                headers={"Authorization": f"Bearer {self.token.value}"},
            ) as response:
                self._check_status(response)
                contents: bytearray = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=max_bytes + 1):
                    remaining: int = max_bytes + 1 - len(contents)
                    contents.extend(chunk[:remaining])
                    if len(contents) > max_bytes:
                        break
                return bytes(contents)
        except httpx.HTTPError:
            raise TelescreenError(ErrorCode.FETCH_FAILED, "Slack request failed") from None

    async def _call[T](
        self,
        method: str,
        params: tuple[tuple[str, str | None], ...],
        adapter: TypeAdapter[T],
    ) -> T:
        """Call and validate one Slack Web API method."""
        request_params: tuple[tuple[str, str], ...] = tuple(
            (key, value) for key, value in params if value is not None
        )
        try:
            response: httpx.Response = await self.http.get(
                f"{self.api_base.rstrip('/')}/{method}",
                params=request_params,
                headers={"Authorization": f"Bearer {self.token.value}"},
            )
        except httpx.HTTPError:
            raise TelescreenError(ErrorCode.FETCH_FAILED, "Slack request failed") from None
        self._check_status(response)
        try:
            status: SlackStatus = TypeAdapter(SlackStatus).validate_json(response.content)
            if not status.ok:
                if status.error in ("message_not_found", "file_not_found"):
                    raise ResourceGoneError("Slack resource is gone")
                raise TelescreenError(ErrorCode.FETCH_FAILED, "Slack API request failed")
            result: T = adapter.validate_json(response.content)
        except (ValidationError, ValueError):
            raise TelescreenError(ErrorCode.FETCH_FAILED, "Slack response was invalid") from None
        return result

    @staticmethod
    def _check_status(response: httpx.Response) -> None:
        """Map HTTP status failures without exposing upstream response data."""
        if response.status_code == 429:
            retry_text: str | None = response.headers.get("Retry-After")
            try:
                retry_after: float | None = float(retry_text) if retry_text else None
            except ValueError:
                retry_after = None
            raise RateLimited(retry_after)
        if response.status_code == 404:
            raise ResourceGoneError("Slack resource is gone")
        if not response.is_success:
            raise TelescreenError(ErrorCode.FETCH_FAILED, "Slack request failed")
