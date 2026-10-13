"""Persistence for collector configurations and scan jobs."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.collectors.codec import (
    CursorState,
    cursor_to_json,
    selector_from_json,
    selector_to_json,
)
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import JsonValue, dump_json, parse_json
from telescreen.core.vocabulary import (
    CollectorKind,
    ErrorCode,
    InventoryMode,
    ScanJobStatus,
    ScanTrigger,
    TriggerMode,
)
from telescreen.db.models import CollectorConfigRow, ScanJobRow
from telescreen.ports.collectors import CollectorConfig, CollectorSelector


class CollectorRepository:
    """Store collector configuration and scan job records."""

    async def get(self, session: AsyncSession, collector_config_id: str) -> CollectorConfig | None:
        """Return a configuration by id."""
        row: CollectorConfigRow | None = await session.get(CollectorConfigRow, collector_config_id)
        return to_config(row) if row is not None else None

    async def get_by_name(self, session: AsyncSession, name: str) -> CollectorConfig | None:
        """Return a configuration by its unique name."""
        row: CollectorConfigRow | None = await session.scalar(
            select(CollectorConfigRow).where(CollectorConfigRow.name == name)
        )
        return to_config(row) if row is not None else None

    async def list(self, session: AsyncSession) -> tuple[CollectorConfig, ...]:
        """Return all configurations in stable name order."""
        rows: list[CollectorConfigRow] = list(
            (
                await session.scalars(select(CollectorConfigRow).order_by(CollectorConfigRow.name))
            ).all()
        )
        return tuple(to_config(row) for row in rows)

    async def enabled(self, session: AsyncSession) -> tuple[CollectorConfigRow, ...]:
        """Return enabled raw rows for scheduler updates."""
        return tuple(
            (
                await session.scalars(
                    select(CollectorConfigRow).where(CollectorConfigRow.enabled.is_(True))
                )
            ).all()
        )

    async def create(
        self,
        session: AsyncSession,
        config_id: str,
        name: str,
        kind: CollectorKind,
        trigger_mode: TriggerMode,
        cron: str | None,
        sweep_cron: str | None,
        sweep_max_concurrency: int,
        selector: CollectorSelector,
        credential_ref: str | None,
        now: datetime,
        inventory_mode: InventoryMode = InventoryMode.SENSITIVE_ONLY,
    ) -> CollectorConfig:
        """Insert a collector config and return its safe DTO."""
        row: CollectorConfigRow = CollectorConfigRow(
            id=config_id,
            name=name,
            kind=kind.value,
            trigger_mode=trigger_mode.value,
            cron=cron,
            sweep_cron=sweep_cron,
            sweep_max_concurrency=sweep_max_concurrency,
            selector_json=dump_json(selector_to_json(selector)),
            credential_ref=credential_ref,
            cursor_json=cursor_to_json(CursorState()),
            enabled=True,
            inventory_mode=inventory_mode.value,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.flush()
        return to_config(row)

    async def update(
        self,
        session: AsyncSession,
        config_id: str,
        trigger_mode: TriggerMode,
        cron: str | None,
        sweep_cron: str | None,
        selector: CollectorSelector,
        credential_ref: str | None,
        enabled: bool,
        now: datetime,
        inventory_mode: InventoryMode = InventoryMode.SENSITIVE_ONLY,
    ) -> CollectorConfig:
        """Update editable collector fields."""
        row: CollectorConfigRow | None = await session.get(CollectorConfigRow, config_id)
        if row is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "collector not found")
        row.trigger_mode = trigger_mode.value
        row.cron = cron
        row.sweep_cron = sweep_cron
        row.selector_json = dump_json(selector_to_json(selector))
        row.credential_ref = credential_ref
        row.enabled = enabled
        row.inventory_mode = inventory_mode.value
        row.updated_at = now
        await session.flush()
        return to_config(row)

    async def set_cursor(
        self, session: AsyncSession, config_id: str, state: CursorState, now: datetime
    ) -> None:
        """Persist cursor and watch state."""
        row: CollectorConfigRow | None = await session.get(CollectorConfigRow, config_id)
        if row is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "collector not found")
        row.cursor_json = cursor_to_json(state)
        row.updated_at = now

    async def create_scan_job(
        self,
        session: AsyncSession,
        collector_config_id: str | None,
        source: str,
        trigger: ScanTrigger,
        started_at: datetime,
    ) -> str:
        """Create a running scan job."""
        job_id: str = new_id()
        session.add(
            ScanJobRow(
                id=job_id,
                collector_config_id=collector_config_id,
                source=source,
                trigger=trigger.value,
                status=ScanJobStatus.RUNNING.value,
                started_at=started_at,
            )
        )
        await session.flush()
        return job_id

    async def finish_scan_job(
        self,
        session: AsyncSession,
        scan_job_id: str,
        status: ScanJobStatus,
        resources_scanned: int,
        resources_skipped: int,
        findings_count: int,
        error_code: ErrorCode | None,
        finished_at: datetime,
    ) -> None:
        """Finish a scan job with count-only results."""
        row: ScanJobRow | None = await session.get(ScanJobRow, scan_job_id)
        if row is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "scan job not found")
        row.status = status.value
        row.resources_scanned = resources_scanned
        row.resources_skipped = resources_skipped
        row.findings_count = findings_count
        row.error_code = error_code.value if error_code is not None else None
        row.finished_at = finished_at
        await session.flush()


def to_config(row: CollectorConfigRow) -> CollectorConfig:
    """Convert a persisted row to a typed config without exposing cursor state."""
    try:
        kind: CollectorKind = CollectorKind(row.kind)
        trigger_mode: TriggerMode = TriggerMode(row.trigger_mode)
        value: JsonValue = parse_json(row.selector_json)
        selector: CollectorSelector = selector_from_json(kind, value)
    except ValueError:
        raise TelescreenError(
            ErrorCode.VALIDATION_ERROR, "invalid stored collector config"
        ) from None
    return CollectorConfig(
        row.id,
        row.name,
        kind,
        trigger_mode,
        row.cron,
        row.sweep_cron,
        row.sweep_max_concurrency,
        selector,
        row.credential_ref,
        row.enabled,
        InventoryMode(row.inventory_mode),
    )


@dataclass(frozen=True)
class ScanJobStoreImpl:
    """Adapt the collector repository to the shared scan-job port."""

    repository: CollectorRepository

    async def create(
        self,
        session: AsyncSession,
        collector_config_id: str | None,
        source: str,
        trigger: ScanTrigger,
        started_at: datetime,
    ) -> str:
        """Create a scan job."""
        return await self.repository.create_scan_job(
            session, collector_config_id, source, trigger, started_at
        )

    async def finish(
        self,
        session: AsyncSession,
        scan_job_id: str,
        status: ScanJobStatus,
        resources_scanned: int,
        resources_skipped: int,
        findings_count: int,
        error_code: ErrorCode | None,
        finished_at: datetime,
    ) -> None:
        """Finish a scan job."""
        await self.repository.finish_scan_job(
            session,
            scan_job_id,
            status,
            resources_scanned,
            resources_skipped,
            findings_count,
            error_code,
            finished_at,
        )
