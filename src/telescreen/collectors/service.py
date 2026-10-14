"""Collector configuration, webhook routing, and scan enqueue operations."""

import hashlib
import logging
import re
from dataclasses import dataclass, field

from croniter import croniter
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.collectors.codec import CursorState, cursor_from_json, selector_to_json
from telescreen.collectors.health import CollectorHealthRecorder
from telescreen.collectors.registry import CollectorRegistry
from telescreen.collectors.repository import CollectorRepository
from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, dump_json, json_str, parse_json
from telescreen.core.vocabulary import (
    ActorKind,
    CollectorKind,
    EngineTaskKind,
    ErrorCode,
    InventoryMode,
    ScanTrigger,
    TriggerMode,
)
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.collectors import (
    CollectorAdapter,
    CollectorConfig,
    CollectorSecrets,
    CollectorSelector,
    EventCheck,
    EventCheckKind,
    EventResources,
    InboundWebhook,
    ResourceRef,
    ScanJobStore,
    WatchState,
)
from telescreen.ports.inventory import InventoryReconciler, NoopInventoryReconciler
from telescreen.ports.workflow import TaskQueue, TaskSpec

logger: logging.Logger = logging.getLogger(__name__)
_SLUG: re.Pattern[str] = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SECRET_REF: re.Pattern[str] = re.compile(r"^(env|file):")


@dataclass(frozen=True)
class WebhookOutcome:
    """Safe HTTP status and optional challenge response."""

    status: int
    body: str | None = None


@dataclass(frozen=True)
class CollectorService:
    """Manage configs and enqueue transient scan work."""

    registry: CollectorRegistry
    queue: TaskQueue
    scan_jobs: ScanJobStore
    repository: CollectorRepository
    fingerprinter: Fingerprinter
    redactor: Redactor
    audit: AuditLog
    clock: Clock
    health: CollectorHealthRecorder | None = None
    inventory: InventoryReconciler = field(default_factory=NoopInventoryReconciler)

    async def get(self, session: AsyncSession, collector_config_id: str) -> CollectorConfig | None:
        """Read a config by id."""
        return await self.repository.get(session, collector_config_id)

    async def get_by_name(self, session: AsyncSession, name: str) -> CollectorConfig | None:
        """Read a config by name."""
        return await self.repository.get_by_name(session, name)

    async def list(self, session: AsyncSession) -> tuple[CollectorConfig, ...]:
        """List collector configs."""
        return await self.repository.list(session)

    async def create(
        self,
        session: AsyncSession,
        name: str,
        kind: CollectorKind,
        trigger_mode: TriggerMode,
        cron: str | None,
        sweep_cron: str | None,
        selector: CollectorSelector,
        credential_ref: str | None,
        actor_ref: str,
        inventory_mode: InventoryMode | None = None,
    ) -> CollectorConfig:
        """Validate and create a collector config."""
        self._validate(name, kind, trigger_mode, cron, sweep_cron, selector, credential_ref)
        config: CollectorConfig = await self.repository.create(
            session,
            _id(),
            name,
            kind,
            trigger_mode,
            cron,
            sweep_cron,
            16,
            selector,
            credential_ref,
            self.clock.now(),
            inventory_mode or InventoryMode.SENSITIVE_ONLY,
        )
        await self._audit(session, "collector.created", config.id, actor_ref, config.inventory_mode)
        return config

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
        actor_ref: str,
        inventory_mode: InventoryMode | None = None,
    ) -> CollectorConfig:
        """Validate and update a collector config."""
        current: CollectorConfig | None = await self.repository.get_locked(session, config_id)
        if current is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "collector not found")
        self._validate(
            current.name, current.kind, trigger_mode, cron, sweep_cron, selector, credential_ref
        )
        config: CollectorConfig = await self.repository.update(
            session,
            config_id,
            trigger_mode,
            cron,
            sweep_cron,
            selector,
            credential_ref,
            enabled,
            self.clock.now(),
            inventory_mode or current.inventory_mode,
        )
        if (
            current.inventory_mode is not InventoryMode.OFF
            and config.inventory_mode is InventoryMode.OFF
        ):
            await self.inventory.stop_tracking(session, config.id, actor_ref)
        await self._audit(session, "collector.updated", config.id, actor_ref, config.inventory_mode)
        return config

    def _validate(
        self,
        name: str,
        kind: CollectorKind,
        trigger_mode: TriggerMode,
        cron: str | None,
        sweep_cron: str | None,
        selector: CollectorSelector,
        credential_ref: str | None,
    ) -> None:
        """Validate config without echoing user-provided values."""
        if not _SLUG.fullmatch(name):
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid collector config")
        if trigger_mode in (TriggerMode.SCHEDULE, TriggerMode.BOTH) and not _valid_cron(cron):
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid collector schedule")
        if sweep_cron is not None and not _valid_cron(sweep_cron):
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid collector sweep schedule")
        if credential_ref is not None and not _SECRET_REF.match(credential_ref):
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid credential reference")
        try:
            encoded: str = dump_json(selector_to_json(selector))
            from telescreen.collectors.codec import selector_from_json

            selector_from_json(kind, parse_json(encoded))
        except (TypeError, ValueError):
            raise TelescreenError(
                ErrorCode.VALIDATION_ERROR, "invalid collector selector"
            ) from None
        if self.redactor.has_hits(encoded):
            raise TelescreenError(
                ErrorCode.RAW_VALUE_DETECTED, "collector selector contains sensitive data"
            )

    async def enqueue_run(
        self, session: AsyncSession, config_id: str, trigger: ScanTrigger
    ) -> bool:
        """Enqueue a manual or scheduled incremental scan."""
        config: CollectorConfig | None = await self.repository.get(session, config_id)
        if config is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "collector not found")
        now: str = self.clock.now().isoformat()
        return await self.queue.enqueue(
            session,
            TaskSpec(
                EngineTaskKind.COLLECTOR_SCAN,
                config.id,
                _task_payload(config.id, "changes", trigger=trigger),
                self.clock.now(),
                f"collector:{config.id}:{trigger.value}:{now}",
            ),
        )

    async def handle_webhook(
        self,
        session: AsyncSession,
        path_kind: CollectorKind,
        event: InboundWebhook,
        secrets: CollectorSecrets,
    ) -> WebhookOutcome:
        """Verify and route a webhook, enqueueing only safe refs."""
        adapter: CollectorAdapter = self.registry.adapter(path_kind)
        check: EventCheck = await adapter.verify_event(event, secrets)
        if check.kind is EventCheckKind.INVALID:
            return WebhookOutcome(401)
        if check.kind is EventCheckKind.CHALLENGE:
            return WebhookOutcome(200, check.response_body)
        kinds: tuple[CollectorKind, ...] = (
            (CollectorKind.GDRIVE, CollectorKind.GDOCS, CollectorKind.GSHEETS)
            if path_kind is CollectorKind.GDRIVE
            else (path_kind,)
        )
        configs: tuple[CollectorConfig, ...] = tuple(
            config
            for config in await self.repository.list(session)
            if config.enabled
            and config.trigger_mode in (TriggerMode.EVENT, TriggerMode.BOTH)
            and config.kind in kinds
        )
        event_id: str = hashlib.sha256(event.body).hexdigest()
        for config in configs:
            config_state: CursorState = cursor_from_json(
                await self._cursor_json(session, config.id)
            )
            if check.config_hint is not None and (
                config_state.watch is None or config_state.watch.channel_id != check.config_hint
            ):
                continue
            if self.health is not None:
                await self.health.event_received(session, config.id, self.clock.now())
            resources: EventResources = await self.registry.adapter(
                config.kind
            ).resources_from_event(event, config)
            for ref in resources.changed:
                await self._enqueue_ref(
                    session, config, ref, event_id, "resource", ScanTrigger.EVENT
                )
            for ref in resources.removed:
                await self._enqueue_ref(
                    session, config, ref, event_id, "removed", ScanTrigger.EVENT
                )
            if resources.poll_changes:
                await self.queue.enqueue(
                    session,
                    TaskSpec(
                        EngineTaskKind.COLLECTOR_SCAN,
                        config.id,
                        _task_payload(config.id, "changes", trigger=ScanTrigger.EVENT),
                        self.clock.now(),
                        f"collector:{config.id}:changes:{event_id}",
                    ),
                )
        return WebhookOutcome(200)

    async def start_watch(self, session: AsyncSession, config_id: str) -> None:
        """Create or renew the provider watch and persist its state."""
        config: CollectorConfig | None = await self.repository.get(session, config_id)
        if config is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "collector not found")
        current: CursorState = cursor_from_json(await self._cursor_json(session, config_id))
        watch: WatchState | None = await self.registry.adapter(config.kind).renew_watch(
            config, current.watch
        )
        await self.repository.set_cursor(
            session,
            config_id,
            CursorState(current.cursor, watch, current.last_sweep_fire),
            self.clock.now(),
        )
        if self.health is not None and watch is not None:
            await self.health.watch_started(session, config_id, watch.expires_at, self.clock.now())

    async def _enqueue_ref(
        self,
        session: AsyncSession,
        config: CollectorConfig,
        ref: ResourceRef,
        event_id: str,
        mode: str,
        trigger: ScanTrigger,
    ) -> None:
        """Enqueue a reference only after checking its opaque JSON for leaks."""
        if self.redactor.has_hits(ref.ref_json):
            logger.warning("collector ref rejected: %s", ErrorCode.RAW_VALUE_DETECTED.value)
            return
        payload: JsonValue = _task_payload(config.id, mode, ref.ref_json, trigger)
        resource_key: str = self.fingerprinter.resource_key(config.kind.value, ref.raw_locator)
        await self.queue.enqueue(
            session,
            TaskSpec(
                EngineTaskKind.COLLECTOR_SCAN,
                config.id,
                payload,
                self.clock.now(),
                f"collector:{config.id}:{resource_key}:{event_id}",
            ),
        )

    async def _cursor_json(self, session: AsyncSession, config_id: str) -> str | None:
        """Read cursor JSON from the owned config row."""
        from telescreen.db.models import CollectorConfigRow

        row: CollectorConfigRow | None = await session.get(CollectorConfigRow, config_id)
        return row.cursor_json if row is not None else None

    async def _audit(
        self,
        session: AsyncSession,
        action: str,
        config_id: str,
        actor_ref: str,
        inventory_mode: InventoryMode,
    ) -> None:
        """Append a count-free audit event using only safe identifiers."""
        from telescreen.core.json import json_object, json_str

        await self.audit.append(
            session,
            AuditEntry(
                ActorKind.MEMBER,
                actor_ref,
                action,
                "collector",
                config_id,
                action,
                json_object((("inventory_mode", json_str(inventory_mode.value)),)),
            ),
        )


def _valid_cron(value: str | None) -> bool:
    """Return whether a five-field cron expression is valid."""
    return value is not None and len(value.split()) == 5 and croniter.is_valid(value)


def _task_payload(
    config_id: str,
    mode: str,
    ref_json: str | None = None,
    trigger: ScanTrigger | None = None,
) -> JsonValue:
    """Build a content-free collector task payload."""
    from telescreen.core.json import json_object

    members: list[tuple[str, JsonValue]] = [
        ("config_id", json_str(config_id)),
        ("mode", json_str(mode)),
    ]
    if ref_json is not None:
        members.append(("ref_json", json_str(ref_json)))
    if trigger is not None:
        members.append(("trigger", json_str(trigger.value)))
    return json_object(tuple(members))


def _id() -> str:
    """Create a stable-format UUID identifier."""
    from telescreen.core.ids import new_id

    return new_id()
