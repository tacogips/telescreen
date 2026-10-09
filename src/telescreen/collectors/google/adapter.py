"""Drive-backed CollectorAdapter implementations for gdrive, gdocs and gsheets."""

import hashlib
import hmac
import json
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol
from uuid import uuid4

from telescreen.collectors.google.drive import DriveApi, DriveFile, DrivePage, WatchResponse
from telescreen.collectors.google.sheets import SheetsApi, SheetValues
from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonKind, JsonValue, parse_json
from telescreen.core.vocabulary import CollectorKind, ErrorCode
from telescreen.ports.collectors import (
    ChangePage,
    CollectorConfig,
    CollectorCursor,
    CollectorSecrets,
    CollectorSelector,
    DriveSelector,
    EventCheck,
    EventCheckKind,
    EventResources,
    InboundWebhook,
    RateLimited,
    ResourceContent,
    ResourceGoneError,
    ResourceRef,
    Segment,
    WatchState,
)

GOOGLE_DOCUMENT: str = "application/vnd.google-apps.document"
GOOGLE_SPREADSHEET: str = "application/vnd.google-apps.spreadsheet"
GOOGLE_NATIVE_PREFIX: str = "application/vnd.google-apps."


class DriveFactory(Protocol):
    """Create a Drive API client for an operation."""

    def __call__(self) -> DriveApi:
        """Return a Drive client."""
        ...


class SheetsFactory(Protocol):
    """Create a Sheets API client for an operation."""

    def __call__(self) -> SheetsApi:
        """Return a Sheets client."""
        ...


@dataclass
class GoogleAdapter:
    """Google collector implementation shared by the three Drive resource kinds."""

    kind: CollectorKind
    drive_factory: DriveFactory
    sheets_factory: SheetsFactory
    clock: Clock
    channel_token_key: bytes = field(repr=False)
    public_base_url: str | None = None
    max_bytes: int = 5 * 1024 * 1024

    async def verify_event(self, event: InboundWebhook, secrets: CollectorSecrets) -> EventCheck:
        """Verify the Drive channel token using constant-time HMAC comparison."""
        channel_id: str | None = event.header("X-Goog-Channel-ID")
        token: str | None = event.header("X-Goog-Channel-Token")
        if channel_id is None or token is None:
            return EventCheck(EventCheckKind.INVALID)
        expected: str = hmac.new(
            secrets.channel_token_key or self.channel_token_key,
            b"drive-channel\0" + channel_id.encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, token):
            return EventCheck(EventCheckKind.INVALID)
        if event.header("X-Goog-Resource-State") == "sync":
            return EventCheck(EventCheckKind.VALID)
        return EventCheck(EventCheckKind.VALID, config_hint=channel_id)

    async def resources_from_event(
        self, event: InboundWebhook, config: CollectorConfig
    ) -> EventResources:
        """Request incremental polling because Drive events contain no file ids."""
        return EventResources((), (), poll_changes=True)

    async def list_changed(
        self, config: CollectorConfig, cursor: CollectorCursor | None
    ) -> ChangePage:
        """List changed resources from Drive's durable changes cursor."""
        drive: DriveApi = self.drive_factory()
        if cursor is None:
            start_token: str = await drive.get_start_page_token()
            return ChangePage((), (), CollectorCursor(start_token), False, False)
        page: DrivePage = await drive.changes_list(cursor.value)
        changed: list[ResourceRef] = []
        removed: list[ResourceRef] = []
        selector: DriveSelector = _drive_selector(config.selector)
        for change in page.changes:
            reference: ResourceRef = self._ref(change.file_id)
            file: DriveFile | None = change.file
            if (
                change.removed
                or file is None
                or file.trashed
                or not _matches(file, selector, self.kind)
            ):
                removed.append(reference)
            else:
                changed.append(reference)
        next_value: str | None = page.next_page_token or page.new_start_page_token
        next_cursor: CollectorCursor | None = (
            CollectorCursor(next_value) if next_value is not None else None
        )
        return ChangePage(
            tuple(changed),
            tuple(removed),
            next_cursor,
            False,
            page.next_page_token is not None,
        )

    async def fetch(self, config: CollectorConfig, ref: ResourceRef) -> ResourceContent:
        """Fetch selected content transiently and return text segments for scanning."""
        return await self.fetch_as(config, ref, None)

    async def fetch_as(
        self, config: CollectorConfig, ref: ResourceRef, subject: str | None
    ) -> ResourceContent:
        """Fetch content while impersonating a Workspace user during a domain sweep."""
        try:
            return await self._fetch_content(config, ref, subject)
        except (ResourceGoneError, RateLimited):
            raise
        except TelescreenError:
            return self._content(config, ref, "", (), False, False)

    async def _fetch_content(
        self, config: CollectorConfig, ref: ResourceRef, subject: str | None
    ) -> ResourceContent:
        """Fetch content for a sweep while preserving typed failures and size skips."""
        drive: DriveApi = self.drive_factory()
        try:
            file: DriveFile = await drive.files_get_meta(_file_id(ref), subject)
            if file.trashed or not _matches(file, _drive_selector(config.selector), self.kind):
                raise TelescreenError(ErrorCode.NOT_FOUND, "Google Drive resource is unavailable")
            if self.kind is CollectorKind.GDOCS:
                payload: bytes = await drive.files_export(file.id, subject=subject)
                segments: tuple[Segment, ...] = _line_segments(payload.decode("utf-8"))
            elif self.kind is CollectorKind.GSHEETS:
                sheets: SheetsApi = self.sheets_factory()
                titles: tuple[str, ...] = await sheets.sheet_titles(file.id, subject)
                ranges: tuple[str, ...] = tuple(
                    f"'{title.replace(chr(39), chr(39) * 2)}'!A:ZZZ" for title in titles
                )
                values: tuple[SheetValues, ...] = await sheets.values_batch_get(
                    file.id, ranges, subject
                )
                segments = _sheet_segments(values)
                payload = "".join(segment.text for segment in segments).encode("utf-8")
            else:
                payload = await drive.files_get_media(file.id, subject)
                if not _text_like(file.mime_type) or len(payload) > self.max_bytes:
                    return self._content(config, ref, file.name, (), False, True)
                segments = _line_segments(payload.decode("utf-8"))
            complete: bool = len(payload) <= self.max_bytes
            return self._content(
                config,
                ref,
                file.name,
                segments if complete else (),
                complete,
                True,
            )
        except UnicodeDecodeError:
            raise TelescreenError(
                ErrorCode.FETCH_FAILED, "Google Drive content is not UTF-8"
            ) from None

    async def renew_watch(
        self, config: CollectorConfig, current: WatchState | None
    ) -> WatchState | None:
        """Replace a Drive changes watch with a fresh seven-day channel."""
        if self.public_base_url is None:
            raise TelescreenError(ErrorCode.CONFIG_ERROR, "Google watch URL is not configured")
        drive: DriveApi = self.drive_factory()
        if current is not None:
            with suppress(Exception):
                await drive.stop_channel(current.channel_id, current.resource_id)
        channel_id: str = str(uuid4())
        token: str = hmac.new(
            self.channel_token_key,
            b"drive-channel\0" + channel_id.encode(),
            hashlib.sha256,
        ).hexdigest()
        expires_at: datetime = ensure_utc(self.clock.now()) + timedelta(days=7)
        address: str = f"{self.public_base_url.rstrip('/')}/api/collectors/gdrive/webhook"
        response: WatchResponse = await drive.watch_changes(channel_id, token, address, expires_at)
        return WatchState(channel_id, response.resource_id, expires_at)

    def ref_from_json(self, ref_json: str) -> ResourceRef:
        """Parse a stored opaque file reference without accepting extra properties."""
        try:
            value: JsonValue = parse_json(ref_json)
            file_value: JsonValue | None = value.get("file_id")
            file_id: str | None = file_value.as_str() if file_value is not None else None
            if (
                value.kind is not JsonKind.OBJECT
                or value.member_names() != ("file_id",)
                or not file_id
            ):
                raise ValueError
        except (TelescreenError, ValueError):
            raise TelescreenError(
                ErrorCode.VALIDATION_ERROR, "Google resource reference is invalid"
            ) from None
        return self._ref(file_id)

    def _ref(self, file_id: str) -> ResourceRef:
        """Create a kind-specific opaque file reference."""
        return ResourceRef(
            self.kind,
            json.dumps({"file_id": file_id}, separators=(",", ":")),
            f"{self.kind.value}:{file_id}",
        )

    def _content(
        self,
        config: CollectorConfig,
        ref: ResourceRef,
        title: str,
        segments: tuple[Segment, ...],
        complete: bool,
        sweep_covered: bool,
    ) -> ResourceContent:
        """Create a transient source payload with sweep coverage derived from selector."""
        return ResourceContent(
            source=self.kind.value,
            collector_config_id=config.id,
            segments=segments,
            complete=complete,
            sweep_covered=sweep_covered,
            raw_locator=ref.raw_locator,
            display_title=title,
            ref=ref,
        )


def build_google_adapters(
    drive_factory: DriveFactory,
    sheets_factory: SheetsFactory,
    clock: Clock,
    channel_token_key: bytes,
    public_base_url: str | None,
    max_bytes: int,
) -> tuple[GoogleAdapter, GoogleAdapter, GoogleAdapter]:
    """Create the three separately registered Google Drive-backed adapter kinds."""
    drive: GoogleAdapter = GoogleAdapter(
        CollectorKind.GDRIVE,
        drive_factory,
        sheets_factory,
        clock,
        channel_token_key,
        public_base_url,
        max_bytes,
    )
    docs: GoogleAdapter = GoogleAdapter(
        CollectorKind.GDOCS,
        drive_factory,
        sheets_factory,
        clock,
        channel_token_key,
        public_base_url,
        max_bytes,
    )
    sheets: GoogleAdapter = GoogleAdapter(
        CollectorKind.GSHEETS,
        drive_factory,
        sheets_factory,
        clock,
        channel_token_key,
        public_base_url,
        max_bytes,
    )
    return drive, docs, sheets


def _drive_selector(selector: CollectorSelector) -> DriveSelector:
    """Return a Drive selector, using the empty selector for malformed configs."""
    if isinstance(selector, DriveSelector):
        return selector
    return DriveSelector((), (), ())


def _matches(file: DriveFile, selector: DriveSelector, kind: CollectorKind) -> bool:
    """Check the selected mime kind and Drive locations."""
    if kind is CollectorKind.GDOCS and file.mime_type != GOOGLE_DOCUMENT:
        return False
    if kind is CollectorKind.GSHEETS and file.mime_type != GOOGLE_SPREADSHEET:
        return False
    if kind is CollectorKind.GDRIVE and not _text_like(file.mime_type):
        return False
    if selector.mime_types and file.mime_type not in selector.mime_types:
        return False
    if not selector.drive_ids and not selector.folder_ids:
        return True
    return file.drive_id in selector.drive_ids or any(
        parent in selector.folder_ids for parent in file.parents
    )


def _file_id(ref: ResourceRef) -> str:
    """Return the file id from an adapter-owned ref JSON payload."""
    value: JsonValue = parse_json(ref.ref_json)
    file_value: JsonValue | None = value.get("file_id")
    file_id: str | None = file_value.as_str() if file_value is not None else None
    if not file_id:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "Google resource reference is invalid")
    return file_id


def _text_like(mime_type: str) -> bool:
    """Return whether a non-native Drive file can safely be decoded as text."""
    return (
        mime_type.startswith("text/")
        or mime_type
        in (
            "application/json",
            "application/csv",
            "text/csv",
            "application/yaml",
            "application/xml",
        )
        or mime_type.endswith("+xml")
        or mime_type.endswith("+json")
    )


def _line_segments(text: str) -> tuple[Segment, ...]:
    """Split decoded file content into one transient segment per line."""
    return tuple(Segment(f"L{index}", line) for index, line in enumerate(text.splitlines(), 1))


def _sheet_segments(values: tuple[SheetValues, ...]) -> tuple[Segment, ...]:
    """Convert Sheets values to per-cell text segments."""
    segments: list[Segment] = []
    for sheet in values:
        range_name: str = sheet.range
        for row_index, row in enumerate(sheet.values, 1):
            for column_index, value in enumerate(row, 1):
                text: str = str(value)
                if text:
                    sheet_name: str = range_name.split("!", maxsplit=1)[0].strip("'")
                    segments.append(Segment(f"{sheet_name}!R{row_index}C{column_index}", text))
    return tuple(segments)
