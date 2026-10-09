"""Police task handlers for map-reduce partition and shard scanning."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

from telescreen_police_sdk import _json
from telescreen_police_sdk.models import (
    MaskedFinding,
    PartitionResult,
    ResultEnvelope,
    ResultError,
    ResultStatus,
    ShardStats,
    TaskEnvelope,
)
from telescreen_police_sdk.worker import TaskHandler


@dataclass(frozen=True)
class ResourceOutcome:
    """Countable result for one enumerated resource."""

    locator: str = field(repr=False)
    findings: tuple[MaskedFinding, ...] = ()
    completed: bool = True


type PartitionFunction = Callable[[TaskEnvelope], Awaitable[PartitionResult]]
type ScanFunction = Callable[[_json.Json], AsyncIterator[ResourceOutcome]]


@dataclass(frozen=True)
class SweepHandler:
    """Build task handlers for partition pages and concurrent shard scans."""

    partition_fn: PartitionFunction
    scan_fn: ScanFunction

    def handlers(self) -> tuple[tuple[str, TaskHandler], ...]:
        """Return handlers keyed by the contract task types."""
        return (("partition", self.partition), ("scan_shard", self.scan_shard))

    async def partition(self, task: TaskEnvelope) -> ResultEnvelope:
        """Run partitioning and reject pages larger than the contract limit."""
        try:
            page: PartitionResult = await self.partition_fn(task)
            if len(page.shards) > 1000:
                return self._failed(task.task_id, "partition_too_large")
            return ResultEnvelope(task.task_id, ResultStatus.COMPLETED, payload=page.to_json())
        except Exception:
            return self._failed(task.task_id, "partition_failed")

    async def scan_shard(self, task: TaskEnvelope) -> ResultEnvelope:
        """Scan each resource and account for completion or failure."""
        resources_scanned: int = 0
        resources_failed: int = 0
        complete_locators: list[str] = []
        findings: list[MaskedFinding] = []
        try:
            async for outcome in self.scan_fn(task.input):
                if outcome.completed:
                    resources_scanned += 1
                    complete_locators.append(outcome.locator)
                    findings.extend(outcome.findings)
                else:
                    resources_failed += 1
            stats: ShardStats = ShardStats(resources_scanned, resources_failed, 0)
            return ResultEnvelope(
                task.task_id,
                ResultStatus.COMPLETED,
                findings=tuple(findings),
                stats=stats,
                complete_locators=tuple(complete_locators),
            )
        except Exception:
            resources_failed += 1
            stats = ShardStats(resources_scanned, resources_failed, 0)
            return ResultEnvelope(
                task.task_id,
                ResultStatus.COMPLETED,
                findings=tuple(findings),
                stats=stats,
                complete_locators=tuple(complete_locators),
            )

    @staticmethod
    def _failed(task_id: str, code: str) -> ResultEnvelope:
        """Return a static failure envelope."""
        return ResultEnvelope(
            task_id,
            ResultStatus.FAILED,
            error=ResultError(code, "Police task failed"),
        )
