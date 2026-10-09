"""Collector sweep launcher used by collector schedules and APIs."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import (
    CollectorKind,
    ErrorCode,
    MapJobStatus,
    MapOrigin,
    ScanTrigger,
)
from telescreen.db.models import MapJobRow
from telescreen.ports.collectors import CollectorConfig, CollectorConfigReader
from telescreen.ports.sweeps import SweepLauncher
from telescreen.workflow.mapreduce.service import MapJobService
from telescreen.workflow.mapreduce.spec import spec_for_collector_sweep


@dataclass(frozen=True)
class MapSweepLauncher(SweepLauncher):
    """Start one non-overlapping collector sweep per configuration."""

    service: MapJobService
    sessions: async_sessionmaker[AsyncSession]
    configs: CollectorConfigReader
    clock: Clock

    async def start_collector_sweep(self, collector_config_id: str, trigger: ScanTrigger) -> str:
        """Validate sweep capability and atomically create a collector job."""
        async with self.sessions.begin() as session:
            config: CollectorConfig | None = await self.configs.get(session, collector_config_id)
            if config is None:
                raise TelescreenError(ErrorCode.NOT_FOUND, "collector config not found")
            if config.kind not in (
                CollectorKind.SLACK,
                CollectorKind.GDRIVE,
                CollectorKind.GDOCS,
                CollectorKind.GSHEETS,
                CollectorKind.GITHUB,
                CollectorKind.POLICE,
            ):
                raise TelescreenError(ErrorCode.SWEEP_UNSUPPORTED, "collector cannot be swept")
            active: MapJobRow | None = await session.scalar(
                select(MapJobRow)
                .where(
                    MapJobRow.collector_config_id == collector_config_id,
                    MapJobRow.origin == MapOrigin.COLLECTOR.value,
                    MapJobRow.status.in_(
                        (
                            MapJobStatus.PARTITIONING.value,
                            MapJobStatus.RUNNING.value,
                            MapJobStatus.REDUCING.value,
                        )
                    ),
                )
                .limit(1)
            )
            if active is not None:
                raise TelescreenError(ErrorCode.CONFLICT, "collector sweep already active")
            return await self.service.create_job(
                session,
                MapOrigin.COLLECTOR,
                collector_config_id,
                None,
                spec_for_collector_sweep(config),
            )
