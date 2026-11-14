"""Workspace partitioning and per-conversation Slack sweep streaming."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from telescreen.collectors.slack.adapter import SlackAdapter
from telescreen.collectors.slack.client import (
    ChannelListResponse,
    FileListResponse,
    HistoryResponse,
    SlackApi,
    SlackConversationInfo,
    SlackFile,
    SlackPermissionDenied,
)
from telescreen.collectors.slack.exposure import slack_exposure
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, json_object, json_str
from telescreen.core.vocabulary import (
    CoverageState,
    ErrorCode,
    IdentityKind,
    PlacementKind,
    UnsupportedReason,
)
from telescreen.ports.collectors import (
    CollectorConfig,
    Coverage,
    Exposure,
    OwnerIdentity,
    PlacementRef,
    RateLimited,
    ResourceContent,
    ResourceGoneError,
    ResourceRef,
    Segment,
    SlackSelector,
)
from telescreen.ports.sweeps import (
    PartitionPage,
    PartitionRequest,
    ShardEntry,
    ShardEntryKind,
    ShardSpec,
)


@dataclass
class SlackSweep:
    """Implement Slack's optional SweepCapable partition and shard interface."""

    api_factory: Callable[[CollectorConfig], SlackApi]
    max_file_bytes: int

    async def partition(self, config: CollectorConfig, request: PartitionRequest) -> PartitionPage:
        """Return one page of deterministic conversation shards."""
        selector: SlackSelector = (
            request.selector
            if isinstance(request.selector, SlackSelector)
            else SlackAdapter._selector(config)
        )
        api: SlackApi = self._api(config)
        team_id: str = await api.team_id()
        page: ChannelListResponse = await api.conversations_list(
            request.cursor, "public_channel,private_channel"
        )
        shards: list[ShardSpec] = []
        selected: set[str] = set(selector.channel_ids)
        for conversation in page.channels:
            if selected and conversation.id not in selected:
                continue
            corpus_key: str = f"slack:{team_id}/{conversation.id}"
            shard_key: str = hashlib.sha256(corpus_key.encode()).hexdigest()
            shards.append(
                ShardSpec(
                    shard_key,
                    json_object((("channel", json_str(conversation.id)),)),
                    1,
                )
            )
        return PartitionPage(tuple(shards), page.response_metadata.next_cursor or None, ())

    def _api(self, config: CollectorConfig) -> SlackApi:
        """Create the typed API client using the assigned adapter factory."""
        return self.api_factory(config)

    def shard_resources(
        self, config: CollectorConfig, shard: ShardSpec
    ) -> AsyncIterator[ShardEntry]:
        """Yield one transient outcome for every message and selected file."""
        return self._stream_shard(config, shard)

    async def _stream_shard(
        self, config: CollectorConfig, shard: ShardSpec
    ) -> AsyncIterator[ShardEntry]:
        """Page a conversation's complete history and optional files."""
        channel_value: JsonValue | None = shard.descriptor.get("channel")
        channel_id: str | None = channel_value.as_str() if channel_value is not None else None
        if channel_id is None:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "Slack shard descriptor was invalid")
        selector: SlackSelector = SlackAdapter._selector(config)
        api: SlackApi = self._api(config)
        team_id: str = await api.team_id()
        adapter: SlackAdapter = SlackAdapter(self._api, self.max_file_bytes)
        channel_info: SlackConversationInfo | None
        try:
            channel_info = await api.conversations_info(channel_id)
        except (RateLimited, SlackPermissionDenied, TelescreenError):
            channel_info = None
        cursor: str | None = None
        while True:
            page: HistoryResponse = await api.conversations_history(
                channel_id, None, None, cursor, 200
            )
            for message in page.messages:
                ref: ResourceRef = adapter._message_ref(team_id, channel_id, message.ts)
                exposure: Exposure | None = slack_exposure(channel_info, team_id)
                content: ResourceContent = ResourceContent(
                    source="slack",
                    collector_config_id=config.id,
                    segments=(Segment("message", message.text),),
                    complete=True,
                    sweep_covered=True,
                    raw_locator=ref.raw_locator,
                    display_title=f"#{channel_id} message",
                    ref=ref,
                    exposure=exposure,
                    owner=(
                        OwnerIdentity(IdentityKind.SLACK_USER, message.user)
                        if message.user is not None
                        else None
                    ),
                    placement=(PlacementRef(PlacementKind.SLACK_CHANNEL, channel_id),),
                    modified_at=adapter._timestamp(
                        message.edited.ts if message.edited is not None else message.ts
                    ),
                    coverage=Coverage(CoverageState.SCANNED),
                )
                yield ShardEntry(ShardEntryKind.FETCHED, ref, content)
            cursor = page.response_metadata.next_cursor or None
            if cursor is None:
                break
        if selector.include_files:
            files_cursor: str | None = None
            while True:
                file_page: FileListResponse = await api.files_list(channel_id, None, files_cursor)
                for slack_file in file_page.files:
                    async for entry in self._file_entry(
                        adapter, api, config, team_id, channel_id, channel_info, slack_file
                    ):
                        yield entry
                files_cursor = file_page.response_metadata.next_cursor or None
                if files_cursor is None:
                    break

    async def _file_entry(
        self,
        adapter: SlackAdapter,
        api: SlackApi,
        config: CollectorConfig,
        team_id: str,
        channel_id: str,
        channel_info: SlackConversationInfo | None,
        listed_file: SlackFile,
    ) -> AsyncIterator[ShardEntry]:
        """Fetch a text file or yield a safe removed/failed outcome."""
        ref: ResourceRef = adapter._file_ref(team_id, listed_file.id)
        try:
            file: SlackFile = await api.files_info(listed_file.id)
            exposure: Exposure | None = slack_exposure(channel_info, team_id, file)
            if file.size > self.max_file_bytes:
                content: ResourceContent = adapter._file_content(
                    config,
                    ref,
                    file,
                    channel_id,
                    exposure,
                    (),
                    False,
                    Coverage(CoverageState.TOO_LARGE),
                )
                yield ShardEntry(ShardEntryKind.FETCHED, ref, content)
                return
            if not adapter._is_text_file(file.mimetype):
                reason: UnsupportedReason = adapter._unsupported_reason(file.mimetype)
                content = adapter._file_content(
                    config,
                    ref,
                    file,
                    channel_id,
                    exposure,
                    (),
                    False,
                    Coverage(CoverageState.UNSUPPORTED, reason),
                )
                yield ShardEntry(ShardEntryKind.FETCHED, ref, content)
                return
            data: bytes = await api.download(file.url_private, self.max_file_bytes)
            if len(data) > self.max_file_bytes:
                content = adapter._file_content(
                    config,
                    ref,
                    file,
                    channel_id,
                    exposure,
                    (),
                    False,
                    Coverage(CoverageState.TOO_LARGE),
                )
                yield ShardEntry(ShardEntryKind.FETCHED, ref, content)
                return
            text: str = data.decode("utf-8")
            segments: tuple[Segment, ...] = tuple(
                Segment(f"L{index}", line) for index, line in enumerate(text.splitlines(), 1)
            )
            content = adapter._file_content(
                config,
                ref,
                file,
                channel_id,
                exposure,
                segments,
                True,
                Coverage(CoverageState.SCANNED),
            )
            yield ShardEntry(ShardEntryKind.FETCHED, ref, content)
        except ResourceGoneError:
            yield ShardEntry(ShardEntryKind.REMOVED, ref)
        except RateLimited:
            raise
        except SlackPermissionDenied:
            yield ShardEntry(ShardEntryKind.FETCHED, ref, adapter._denied_file_content(config, ref))
        except (TelescreenError, UnicodeDecodeError):
            yield ShardEntry(ShardEntryKind.FAILED, ref, error_code=ErrorCode.FETCH_FAILED)
