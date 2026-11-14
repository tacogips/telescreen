"""Slack event, incremental schedule, and resource fetch adapter."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from pydantic import TypeAdapter, ValidationError

from telescreen.collectors.slack.client import (
    FileListResponse,
    HistoryResponse,
    SlackApi,
    SlackFile,
    SlackMessage,
)
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonKind, JsonValue, dump_json, json_object, json_str, parse_json
from telescreen.core.vocabulary import CollectorKind, ErrorCode
from telescreen.ports.collectors import (
    ChangePage,
    CollectorConfig,
    CollectorCursor,
    CollectorSecrets,
    EventCheck,
    EventCheckKind,
    EventResources,
    InboundWebhook,
    ResourceContent,
    ResourceGoneError,
    ResourceRef,
    Segment,
    SlackSelector,
    WatchState,
)


@dataclass(frozen=True)
class SlackMessageEvent:
    """Message fields nested inside a Slack event."""

    ts: str


@dataclass(frozen=True)
class SlackFileEvent:
    """File identity nested inside a Slack event."""

    id: str


@dataclass(frozen=True)
class SlackEventBody:
    """Event-specific fields used to create resource references."""

    type: str
    channel: str | None = None
    channel_id: str | None = None
    subtype: str | None = None
    ts: str | None = None
    deleted_ts: str | None = None
    message: SlackMessageEvent | None = None
    file_id: str | None = None
    file: SlackFileEvent | None = None


@dataclass(frozen=True)
class SlackEnvelope:
    """Top-level Slack Events API payload fields used by this adapter."""

    type: str
    team_id: str | None = None
    challenge: str | None = None
    event: SlackEventBody | None = None


@dataclass(frozen=True)
class SlackRefPayload:
    """JSON representation of a Slack resource reference."""

    type: str
    team: str
    channel: str | None = None
    ts: str | None = None
    file: str | None = None


@dataclass
class SlackAdapter:
    """Implement the collector port for Slack conversations and files."""

    api_factory: Callable[[CollectorConfig], SlackApi]
    max_file_bytes: int
    kind: CollectorKind = CollectorKind.SLACK

    async def verify_event(self, event: InboundWebhook, secrets: CollectorSecrets) -> EventCheck:
        """Verify Slack's signature against the exact body bytes and timestamp."""
        signing_secret: str | None = secrets.signing_secret
        timestamp: str | None = event.header("X-Slack-Request-Timestamp")
        signature: str | None = event.header("X-Slack-Signature")
        if signing_secret is None or timestamp is None or signature is None:
            return EventCheck(EventCheckKind.INVALID)
        try:
            received_timestamp: int = int(timestamp)
        except ValueError:
            return EventCheck(EventCheckKind.INVALID)
        now_timestamp: int = int(event.received_at.timestamp())
        if abs(now_timestamp - received_timestamp) > 300:
            return EventCheck(EventCheckKind.INVALID)
        base: bytes = b"v0:" + timestamp.encode() + b":" + event.body
        digest: str = hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
        expected: str = f"v0={digest}"
        if not hmac.compare_digest(expected, signature):
            return EventCheck(EventCheckKind.INVALID)
        try:
            payload: SlackEnvelope = TypeAdapter(SlackEnvelope).validate_json(event.body)
        except (ValidationError, ValueError):
            return EventCheck(EventCheckKind.INVALID)
        if payload.type == "url_verification" and payload.challenge is not None:
            return EventCheck(EventCheckKind.CHALLENGE, response_body=payload.challenge)
        return EventCheck(EventCheckKind.VALID)

    async def resources_from_event(
        self, event: InboundWebhook, config: CollectorConfig
    ) -> EventResources:
        """Return selected conversation or file refs represented by the webhook."""
        selector: SlackSelector = self._selector(config)
        try:
            payload: SlackEnvelope = TypeAdapter(SlackEnvelope).validate_json(event.body)
        except (ValidationError, ValueError):
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "Slack event was invalid") from None
        if payload.type != "event_callback" or payload.team_id is None or payload.event is None:
            return EventResources((), ())
        update: SlackEventBody = payload.event
        channel_id: str | None = update.channel or update.channel_id
        if channel_id is not None and channel_id not in selector.channel_ids:
            return EventResources((), ())
        if update.type == "message_deleted" or (
            update.type == "message" and update.subtype == "message_deleted"
        ):
            if channel_id is None or update.deleted_ts is None:
                return EventResources((), ())
            return EventResources(
                (), (self._message_ref(payload.team_id, channel_id, update.deleted_ts),)
            )
        if update.type in ("message", "message_changed"):
            timestamp: str | None = update.ts
            if update.type == "message_changed" or update.subtype == "message_changed":
                timestamp = update.message.ts if update.message is not None else update.ts
            if channel_id is None or timestamp is None:
                return EventResources((), ())
            return EventResources((self._message_ref(payload.team_id, channel_id, timestamp),), ())
        if update.type == "file_shared" and selector.include_files:
            if channel_id is None:
                return EventResources((), ())
            file_id: str | None = update.file_id
            if file_id is None and update.file is not None:
                file_id = update.file.id
            if file_id is not None:
                return EventResources((self._file_ref(payload.team_id, file_id),), ())
        if update.type == "file_deleted" and selector.include_files:
            file_id = update.file_id
            if file_id is None and update.file is not None:
                file_id = update.file.id
            if file_id is not None:
                return EventResources((), (self._file_ref(payload.team_id, file_id),))
        return EventResources((), ())

    async def list_changed(
        self, config: CollectorConfig, cursor: CollectorCursor | None
    ) -> ChangePage:
        """List newer messages and files for configured Slack channels."""
        selector: SlackSelector = self._selector(config)
        watermarks: list[tuple[str, str]] = list(self._read_cursor(cursor))
        api: SlackApi = self.api_factory(config)
        team_id: str = await api.team_id()
        changed: list[ResourceRef] = []
        removed: list[ResourceRef] = []
        seen: set[str] = set()
        for channel_id in selector.channel_ids:
            watermark: str | None = self._cursor_value(watermarks, channel_id)
            newest: str | None = watermark
            history_cursor: str | None = None
            while True:
                page: HistoryResponse = await api.conversations_history(
                    channel_id, watermark, None, history_cursor, 200
                )
                for message in page.messages:
                    ref: ResourceRef = self._message_ref(team_id, channel_id, message.ts)
                    self._append_unique(changed, seen, ref)
                    newest = self._max_ts(newest, message.ts)
                history_cursor = page.response_metadata.next_cursor or None
                if history_cursor is None:
                    break
            if selector.include_files:
                files_cursor: str | None = None
                while True:
                    file_page: FileListResponse = await api.files_list(
                        channel_id, watermark, files_cursor
                    )
                    for file in file_page.files:
                        ref = self._file_ref(team_id, file.id)
                        self._append_unique(changed, seen, ref)
                        newest = self._max_ts(newest, file.timestamp)
                    files_cursor = file_page.response_metadata.next_cursor or None
                    if files_cursor is None:
                        break
            if newest is not None:
                watermarks = [entry for entry in watermarks if entry[0] != channel_id]
                watermarks.append((channel_id, newest))
        next_cursor: CollectorCursor = CollectorCursor(
            dump_json(json_object(tuple((key, json_str(value)) for key, value in watermarks)))
        )
        return ChangePage(tuple(changed), tuple(removed), next_cursor, False, False)

    async def fetch(self, config: CollectorConfig, ref: ResourceRef) -> ResourceContent:
        """Fetch one Slack message or supported text file transiently."""
        valid_ref: ResourceRef = self.ref_from_json(ref.ref_json)
        if ref.kind is not CollectorKind.SLACK or valid_ref.raw_locator != ref.raw_locator:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "Slack reference was invalid")
        payload: SlackRefPayload = TypeAdapter(SlackRefPayload).validate_json(ref.ref_json)
        api: SlackApi = self.api_factory(config)
        if payload.type == "message" and payload.channel is not None and payload.ts is not None:
            page: HistoryResponse = await api.conversations_history(
                payload.channel, payload.ts, payload.ts, None, 1, inclusive=True
            )
            message: SlackMessage | None = next(
                (item for item in page.messages if item.ts == payload.ts), None
            )
            if message is None:
                raise ResourceGoneError("Slack message is gone")
            return ResourceContent(
                source="slack",
                collector_config_id=config.id,
                segments=(Segment("message", message.text),),
                complete=True,
                sweep_covered=payload.channel in self._selector(config).channel_ids,
                raw_locator=ref.raw_locator,
                display_title=f"#{payload.channel} message",
                ref=ref,
            )
        if payload.type == "file" and payload.file is not None:
            file: SlackFile = await api.files_info(payload.file)
            segments: tuple[Segment, ...] = ()
            complete: bool = False
            if file.size <= self.max_file_bytes and self._is_text_file(file.mimetype):
                contents: bytes = await api.download(file.url_private, self.max_file_bytes)
                if len(contents) <= self.max_file_bytes:
                    try:
                        text: str = contents.decode("utf-8")
                    except UnicodeDecodeError:
                        text = ""
                    else:
                        segments = tuple(
                            Segment(f"L{line_number}", line)
                            for line_number, line in enumerate(text.splitlines(), start=1)
                        )
                        complete = True
            return ResourceContent(
                source="slack",
                collector_config_id=config.id,
                segments=segments,
                complete=complete,
                sweep_covered=True,
                raw_locator=ref.raw_locator,
                display_title="Slack file",
                ref=ref,
            )
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "Slack reference was invalid")

    async def renew_watch(
        self, config: CollectorConfig, current: WatchState | None
    ) -> WatchState | None:
        """Slack has no channel watch to create or renew."""
        return None

    def ref_from_json(self, ref_json: str) -> ResourceRef:
        """Rebuild a Slack reference, rejecting malformed or mismatched JSON."""
        try:
            payload: SlackRefPayload = TypeAdapter(SlackRefPayload).validate_json(ref_json)
        except (ValidationError, ValueError):
            raise TelescreenError(
                ErrorCode.VALIDATION_ERROR, "Slack reference was invalid"
            ) from None
        if payload.type == "message" and payload.channel is not None and payload.ts is not None:
            return self._message_ref(payload.team, payload.channel, payload.ts)
        if payload.type == "file" and payload.file is not None:
            return self._file_ref(payload.team, payload.file)
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "Slack reference was invalid")

    @staticmethod
    def _selector(config: CollectorConfig) -> SlackSelector:
        """Return the Slack selector or raise a safe validation error."""
        if not isinstance(config.selector, SlackSelector):
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "Slack selector was invalid")
        return config.selector

    @staticmethod
    def _message_ref(team_id: str, channel_id: str, timestamp: str) -> ResourceRef:
        """Construct a stable Slack message reference."""
        ref_json: str = json.dumps(
            {"type": "message", "team": team_id, "channel": channel_id, "ts": timestamp},
            separators=(",", ":"),
            sort_keys=True,
        )
        return ResourceRef(
            CollectorKind.SLACK,
            ref_json,
            f"slack:{team_id}/{channel_id}/{timestamp}",
        )

    @staticmethod
    def _file_ref(team_id: str, file_id: str) -> ResourceRef:
        """Construct a stable Slack file reference."""
        ref_json: str = json.dumps(
            {"type": "file", "team": team_id, "file": file_id},
            separators=(",", ":"),
            sort_keys=True,
        )
        return ResourceRef(
            CollectorKind.SLACK,
            ref_json,
            f"slack:{team_id}/file/{file_id}",
        )

    @staticmethod
    def _read_cursor(cursor: CollectorCursor | None) -> tuple[tuple[str, str], ...]:
        """Decode the canonical per-channel timestamp object."""
        if cursor is None:
            return ()
        value: JsonValue = parse_json(cursor.value)
        if value.kind is not JsonKind.OBJECT:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "Slack cursor was invalid")
        parsed: list[tuple[str, str]] = []
        for member in value.members:
            timestamp: str | None = member.value.as_str()
            if timestamp is None:
                raise TelescreenError(ErrorCode.VALIDATION_ERROR, "Slack cursor was invalid")
            parsed.append((member.name, timestamp))
        return tuple(parsed)

    @staticmethod
    def _cursor_value(watermarks: list[tuple[str, str]], channel_id: str) -> str | None:
        """Return a channel watermark when present."""
        return next((value for key, value in watermarks if key == channel_id), None)

    @staticmethod
    def _max_ts(current: str | None, candidate: str | None) -> str | None:
        """Compare Slack timestamps as decimals instead of binary floats."""
        if candidate is None:
            return current
        try:
            candidate_value: Decimal = Decimal(candidate)
            current_value: Decimal = (
                Decimal(current) if current is not None else Decimal("-Infinity")
            )
        except InvalidOperation:
            return current
        return candidate if candidate_value > current_value else current

    @staticmethod
    def _append_unique(target: list[ResourceRef], seen: set[str], ref: ResourceRef) -> None:
        """Append each resource identity once while retaining provider order."""
        if ref.raw_locator not in seen:
            seen.add(ref.raw_locator)
            target.append(ref)

    @staticmethod
    def _is_text_file(mimetype: str) -> bool:
        """Return whether a Slack MIME type is safe to decode as UTF-8 text."""
        lowered: str = mimetype.casefold()
        return lowered.startswith("text/") or lowered in (
            "application/json",
            "application/csv",
            "text/csv",
            "application/yaml",
            "application/x-yaml",
        )
