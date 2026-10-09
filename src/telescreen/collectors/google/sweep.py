"""Drive corpus partitioning and streamed shard scans for durable map-reduce sweeps."""

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from telescreen.collectors.google.adapter import GoogleAdapter, _drive_selector, _matches
from telescreen.collectors.google.admin import AdminApi, UsersPage
from telescreen.collectors.google.drive import DriveApi, DriveFile, DriveListPage
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonKind, JsonValue, json_array, json_object, json_str
from telescreen.core.vocabulary import CollectorKind, ErrorCode
from telescreen.ports.collectors import (
    CollectorConfig,
    DriveSelector,
    ResourceContent,
    ResourceGoneError,
    ResourceRef,
)
from telescreen.ports.sweeps import (
    PartitionPage,
    PartitionRequest,
    ShardEntry,
    ShardEntryKind,
    ShardSpec,
    SubCorpus,
)


class DriveAdapterFactory(Protocol):
    """Create Drive clients and retrieve the matching transient-content adapter."""

    def drive(self) -> DriveApi:
        """Return the Drive API client."""
        ...

    def adapter(self, kind: CollectorKind) -> GoogleAdapter:
        """Return the adapter registered for a Google kind."""
        ...


@dataclass
class GoogleSweep:
    """Partition Drive estates and stream one resource result at a time."""

    kind: CollectorKind
    drive_factory: DriveAdapterFactory
    admin: AdminApi | None
    shard_size: int

    async def partition(self, config: CollectorConfig, request: PartitionRequest) -> PartitionPage:
        """Discover corpora or return a page of deterministic file-id shards."""
        selector: DriveSelector = _drive_selector(config.selector)
        if request.corpus_key == "":
            return await self._root_page(selector)
        corpus_key: str = request.corpus_key
        subject: str | None = None
        if corpus_key.startswith("user:"):
            user_id: str = corpus_key.removeprefix("user:")
            if self.admin is None:
                raise TelescreenError(
                    ErrorCode.SWEEP_UNSUPPORTED, "Google user corpus is unavailable"
                )
            subject = await self.admin.user_email(user_id)
            corpora: str = "user"
            drive_id: str | None = None
            q: str = self._query("'me' in owners and trashed = false", selector)
        elif corpus_key.startswith("drive:"):
            corpora = "drive"
            drive_id = corpus_key.removeprefix("drive:")
            q = self._query("trashed = false", selector)
        elif corpus_key.startswith("folder:"):
            folder_id: str = corpus_key.removeprefix("folder:")
            corpora = "user"
            drive_id = None
            q = self._query(
                f"'{_escape_query(folder_id)}' in parents and trashed = false", selector
            )
        else:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "Google sweep corpus is invalid")
        drive: DriveApi = self.drive_factory.drive()
        page: DriveListPage = await drive.files_list(
            q,
            corpora,
            drive_id,
            request.cursor,
            1000,
            subject,
        )
        selected_files: list[str] = [
            item.id
            for item in page.files
            if not item.trashed
            and _matches(item, selector, self.kind)
            and _included(item, corpus_key, selector)
        ]
        shards: tuple[ShardSpec, ...] = tuple(
            _shard(corpus_key, tuple(selected_files[offset : offset + self.shard_size]))
            for offset in range(0, len(selected_files), self.shard_size)
        )
        return PartitionPage(shards, page.next_page_token, ())

    def shard_resources(
        self, config: CollectorConfig, shard: ShardSpec
    ) -> AsyncIterator[ShardEntry]:
        """Yield each resource's fetched, removed or failed result in sequence."""

        async def stream() -> AsyncIterator[ShardEntry]:
            corpus_key: str
            file_ids: tuple[str, ...]
            corpus_key, file_ids = _read_descriptor(shard.descriptor)
            subject: str | None = None
            if corpus_key.startswith("user:"):
                if self.admin is None:
                    raise TelescreenError(
                        ErrorCode.SWEEP_UNSUPPORTED, "Google user corpus is unavailable"
                    )
                subject = await self.admin.user_email(corpus_key.removeprefix("user:"))
            drive: DriveApi = self.drive_factory.drive()
            adapter: GoogleAdapter = self.drive_factory.adapter(self.kind)
            for file_id in file_ids:
                ref: ResourceRef = adapter._ref(file_id)
                try:
                    metadata: DriveFile = await drive.files_get_meta(file_id, subject)
                    if metadata.trashed:
                        yield ShardEntry(ShardEntryKind.REMOVED, ref)
                        continue
                    content: ResourceContent = await adapter._fetch_content(config, ref, subject)
                    yield ShardEntry(ShardEntryKind.FETCHED, ref, content)
                except ResourceGoneError:
                    yield ShardEntry(ShardEntryKind.REMOVED, ref)
                except TelescreenError as error:
                    yield ShardEntry(ShardEntryKind.FAILED, ref, error_code=error.code)

        return stream()

    async def _root_page(self, selector: DriveSelector) -> PartitionPage:
        """Discover user and shared-drive corpora or the selector's local corpora."""
        if self.admin is None:
            corpora: tuple[SubCorpus, ...] = tuple(
                [SubCorpus(f"drive:{drive_id}") for drive_id in selector.drive_ids]
                + [SubCorpus(f"folder:{folder_id}") for folder_id in selector.folder_ids]
            )
            return PartitionPage((), None, corpora)
        discovered: list[SubCorpus] = []
        user_cursor: str | None = None
        while True:
            users: UsersPage = await self.admin.users_list(user_cursor)
            discovered.extend(SubCorpus(f"user:{user.id}") for user in users.users)
            if users.next_page_token is None:
                break
            user_cursor = users.next_page_token
        drive_page_token: str | None = None
        while True:
            drives_page: DriveListPage = await self.drive_factory.drive().drives_list(
                drive_page_token, self.admin.admin_subject
            )
            discovered.extend(SubCorpus(f"drive:{drive.id}") for drive in drives_page.drives)
            if drives_page.next_page_token is None:
                break
            drive_page_token = drives_page.next_page_token
        return PartitionPage((), None, tuple(discovered))

    def _query(self, base: str, selector: DriveSelector) -> str:
        """Append the MIME selection clause for the adapter kind."""
        mime: str | None = {
            CollectorKind.GDOCS: "application/vnd.google-apps.document",
            CollectorKind.GSHEETS: "application/vnd.google-apps.spreadsheet",
        }.get(self.kind)
        if mime is None and selector.mime_types:
            mime_clauses: tuple[str, ...] = tuple(
                f"mimeType = '{_escape_query(value)}'" for value in selector.mime_types
            )
            return f"{base} and ({' or '.join(mime_clauses)})"
        if mime is None:
            return base
        return f"{base} and mimeType = '{mime}'"


def _shard(corpus_key: str, file_ids: tuple[str, ...]) -> ShardSpec:
    """Create a stable descriptor containing only corpus and opaque file ids."""
    sorted_ids: tuple[str, ...] = tuple(sorted(file_ids))
    digest: str = hashlib.sha256(f"{corpus_key}\0{','.join(sorted_ids)}".encode()).hexdigest()
    descriptor: JsonValue = json_object(
        (
            ("corpus", json_str(corpus_key)),
            ("file_ids", json_array(tuple(json_str(file_id) for file_id in sorted_ids))),
        )
    )
    return ShardSpec(digest, descriptor, len(sorted_ids))


def _read_descriptor(descriptor: JsonValue) -> tuple[str, tuple[str, ...]]:
    """Validate a stored opaque shard descriptor."""
    corpus_value: JsonValue | None = descriptor.get("corpus")
    ids_value: JsonValue | None = descriptor.get("file_ids")
    corpus: str | None = corpus_value.as_str() if corpus_value is not None else None
    ids: tuple[JsonValue, ...] | None = ids_value.as_list() if ids_value is not None else None
    file_ids: tuple[str, ...] = (
        tuple(item.str_value for item in ids if item.kind is JsonKind.STRING)
        if ids is not None
        else ()
    )
    if (
        descriptor.kind is not JsonKind.OBJECT
        or corpus is None
        or ids is None
        or len(file_ids) != len(ids)
    ):
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "Google shard descriptor is invalid")
    return corpus, file_ids


def _included(file: DriveFile, corpus_key: str, selector: DriveSelector) -> bool:
    """Deduplicate selected-drive/folder overlaps in selector order."""
    if corpus_key.startswith("user:"):
        return True
    if corpus_key.startswith("drive:"):
        return file.drive_id == corpus_key.removeprefix("drive:")
    if file.drive_id is not None and file.drive_id in selector.drive_ids:
        return False
    folder_id: str = corpus_key.removeprefix("folder:")
    folder_index: int = (
        selector.folder_ids.index(folder_id) if folder_id in selector.folder_ids else 0
    )
    earlier: tuple[str, ...] = selector.folder_ids[:folder_index]
    return not any(parent in earlier for parent in file.parents)


def _escape_query(value: str) -> str:
    """Escape quotes in a Drive query literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")
