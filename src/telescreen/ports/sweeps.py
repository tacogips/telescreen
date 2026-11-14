"""Map-reduce sweep DTOs and protocols."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.json import JsonValue
from telescreen.core.vocabulary import (
    ErrorCode,
    RuleClass,
    ScanTrigger,
)
from telescreen.ports.collectors import (
    CollectorConfig,
    CollectorSelector,
    ResourceContent,
    ResourceRef,
)


@dataclass(frozen=True)
class PartitionRequest:
    """Request one page of partitions for a selected corpus."""

    corpus_key: str
    cursor: str | None
    selector: CollectorSelector


@dataclass(frozen=True)
class ShardSpec:
    """Opaque descriptor and deterministic identity for one shard."""

    shard_key: str
    descriptor: JsonValue
    weight: int


@dataclass(frozen=True)
class SubCorpus:
    """A child corpus discovered during partitioning."""

    corpus_key: str


@dataclass(frozen=True)
class PartitionPage:
    """One partitioning response, including child corpora."""

    shards: tuple[ShardSpec, ...]
    next_cursor: str | None
    sub_corpora: tuple[SubCorpus, ...]


class ShardEntryKind(StrEnum):
    """Outcome for one resource enumerated by a shard."""

    FETCHED = "fetched"
    REMOVED = "removed"
    FAILED = "failed"


@dataclass(frozen=True)
class ShardEntry:
    """One resource result emitted while enumerating a shard."""

    kind: ShardEntryKind
    ref: ResourceRef
    content: ResourceContent | None = None
    error_code: ErrorCode | None = None


@dataclass(frozen=True)
class RuleClassCount:
    """Finding count for one rule class."""

    rule_class: RuleClass
    count: int


@dataclass(frozen=True)
class ShardSummary:
    """Count-only reduce result for a completed shard."""

    resources_scanned: int
    resources_skipped: int
    resources_failed: int
    bytes_scanned: int
    findings_inserted: int
    issues_opened: int
    issues_auto_resolved: int
    findings_by_rule_class: tuple[RuleClassCount, ...]

    @staticmethod
    def empty() -> ShardSummary:
        """Return a summary with all counters set to zero."""
        return ShardSummary(0, 0, 0, 0, 0, 0, 0, ())

    def plus(self, other: ShardSummary) -> ShardSummary:
        """Add counters and merge per-class totals in stable order."""
        merged: list[RuleClassCount] = list(self.findings_by_rule_class)
        for incoming in other.findings_by_rule_class:
            for index, existing in enumerate(merged):
                if existing.rule_class is incoming.rule_class:
                    merged[index] = RuleClassCount(
                        existing.rule_class, existing.count + incoming.count
                    )
                    break
            else:
                merged.append(incoming)
        return ShardSummary(
            self.resources_scanned + other.resources_scanned,
            self.resources_skipped + other.resources_skipped,
            self.resources_failed + other.resources_failed,
            self.bytes_scanned + other.bytes_scanned,
            self.findings_inserted + other.findings_inserted,
            self.issues_opened + other.issues_opened,
            self.issues_auto_resolved + other.issues_auto_resolved,
            tuple(merged),
        )


@dataclass(frozen=True)
class ShardFailure:
    """Safe failed-shard result and optional backoff delay."""

    error_code: ErrorCode
    retry_after_seconds: float | None = None


class SweepCapable(Protocol):
    """Optional sweep partitioning capability for a collector adapter."""

    async def partition(self, config: CollectorConfig, request: PartitionRequest) -> PartitionPage:
        """Enumerate one page of deterministic shards."""
        ...

    def shard_resources(
        self, config: CollectorConfig, shard: ShardSpec
    ) -> AsyncIterator[ShardEntry]:
        """Stream resource outcomes for one shard."""
        ...


class CollectorSweeper(Protocol):
    """Start partitions and scan shards for a configured collector."""

    async def partition(self, collector_config_id: str, request: PartitionRequest) -> PartitionPage:
        """Partition a configured collector's corpus."""
        ...

    async def scan_shard(
        self, collector_config_id: str, shard: ShardSpec, scan_job_id: str
    ) -> ShardSummary:
        """Scan one shard and return count-only results."""
        ...


class SweepLauncher(Protocol):
    """Launch collector sweeps from schedules and APIs."""

    async def start_collector_sweep(self, collector_config_id: str, trigger: ScanTrigger) -> str:
        """Start a sweep and return its map job id."""
        ...


class MapResultSink(Protocol):
    """Accept police partition and shard results in a caller transaction."""

    async def partition_page(
        self, session: AsyncSession, police_task_id: str, page: PartitionPage
    ) -> None:
        """Record a police partition page."""
        ...

    async def shard_finished(
        self, session: AsyncSession, police_task_id: str, summary: ShardSummary
    ) -> None:
        """Record a completed police shard."""
        ...

    async def task_failed(
        self, session: AsyncSession, police_task_id: str, failure: ShardFailure
    ) -> None:
        """Record a failed police shard task."""
        ...
