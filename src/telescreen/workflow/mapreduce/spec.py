"""Map job execution limits and collector sweep defaults."""

from dataclasses import dataclass
from datetime import timedelta

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import CollectorKind, ErrorCode
from telescreen.ports.collectors import CollectorConfig, PoliceSelector
from telescreen.workflow.dsl.model import MapStep, MapVia


@dataclass(frozen=True)
class MapJobSpec:
    """Serializable execution policy for one map job."""

    partition_via: MapVia
    map_via: MapVia
    partition_task_type: str | None
    map_task_type: str | None
    partition_labels: tuple[str, ...]
    map_labels: tuple[str, ...]
    max_concurrency: int
    shard_timeout: timedelta
    shard_max_attempts: int
    max_failed_ratio: float
    max_failed_items: int
    timeout: timedelta

    def __post_init__(self) -> None:
        """Reject invalid limits before persisting the spec."""
        if not 1 <= self.max_concurrency <= 1024 or self.shard_max_attempts < 1:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid map limits")
        if not 0 <= self.max_failed_ratio <= 1 or self.max_failed_items < 0:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid map failure bounds")


def spec_for_collector_sweep(config: CollectorConfig) -> MapJobSpec:
    """Choose built-in collector or Police task defaults for a sweep."""
    via: MapVia = MapVia.POLICE if config.kind is CollectorKind.POLICE else MapVia.COLLECTOR
    selector: PoliceSelector | None = (
        config.selector if isinstance(config.selector, PoliceSelector) else None
    )
    if via is MapVia.POLICE and selector is None:
        raise TelescreenError(ErrorCode.VALIDATION_ERROR, "police selector required")
    labels: tuple[str, ...] = selector.labels if selector is not None else ()
    return MapJobSpec(
        via,
        via,
        "partition" if via is MapVia.POLICE else None,
        "scan_shard" if via is MapVia.POLICE else None,
        labels,
        labels,
        config.sweep_max_concurrency,
        timedelta(minutes=15),
        3,
        0.01,
        100,
        timedelta(days=1),
    )


def spec_from_step(step: MapStep) -> MapJobSpec:
    """Build runtime policy from a validated map DSL step."""
    return MapJobSpec(
        step.partition_via,
        step.map_via,
        step.partition_task_type,
        step.map_task_type,
        step.partition_labels,
        step.map_labels,
        step.max_concurrency,
        step.shard_timeout,
        step.shard_max_attempts,
        step.max_failed_ratio,
        step.max_failed_items,
        step.timeout,
    )
