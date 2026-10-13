"""Composition helpers for v1.4 data inventory services and routes."""

from dataclasses import dataclass

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.api.common.context import ApiContext
from telescreen.api.routers.data_asset_detail import (
    DataAssetDetailRouterDeps,
    build_data_asset_detail_router,
)
from telescreen.api.routers.data_asset_values import (
    DataAssetValuesRouterDeps,
    build_data_asset_values_router,
)
from telescreen.api.routers.data_assets import DataAssetsRouterDeps, build_data_assets_router
from telescreen.api.routers.data_fields import DataFieldsRouterDeps, build_data_fields_router
from telescreen.core.clock import Clock
from telescreen.core.config import TelescreenSettings
from telescreen.inventory.actors import InventoryActorDirectory
from telescreen.inventory.assets import AssetService
from telescreen.inventory.export import AssetExporter
from telescreen.inventory.fields import FieldService
from telescreen.inventory.observer import InventoryObserverImpl
from telescreen.inventory.queries import AssetQueries
from telescreen.inventory.reconcile import InventoryReconcilerImpl
from telescreen.inventory.registration import RegistrationService
from telescreen.inventory.repository import InventoryRepository
from telescreen.inventory.retention import DataOverdueHandler, DataOverdueProvider
from telescreen.inventory.values import ValueService, ValueValidator
from telescreen.issues.asset_owner import IssueAssetOwnerResolver
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditLog
from telescreen.ports.collectors import CollectorConfigReader
from telescreen.ports.directory import ActorDirectory, OwnerDirectory, RecipientResolver
from telescreen.ports.issues import ResourceIssueReader
from telescreen.ports.notifications import MemberNotifier, NotificationSender
from telescreen.ports.workflow import ScheduleProvider, TaskHandler, TaskQueue


@dataclass(frozen=True)
class WiringV14Parts:
    """Runtime dependencies needed by v1.4 inventory wiring."""

    sessions: async_sessionmaker[AsyncSession]
    clock: Clock
    settings: TelescreenSettings
    audit: AuditLog
    redactor: Redactor
    fingerprinter: Fingerprinter
    collector_configs: CollectorConfigReader
    issue_reader: ResourceIssueReader
    actors: ActorDirectory
    recipients: RecipientResolver
    owners: OwnerDirectory
    members: MemberNotifier
    sender: NotificationSender
    queue: TaskQueue
    context: ApiContext


@dataclass(frozen=True)
class WiringV14:
    """Built v1.4 services, inventory hooks, and runtime registrations."""

    repository: InventoryRepository
    observer: InventoryObserverImpl
    reconciler: InventoryReconcilerImpl
    queries: AssetQueries
    assets: AssetService
    fields: FieldService
    validator: ValueValidator
    values: ValueService
    registration: RegistrationService
    owner_resolver: IssueAssetOwnerResolver
    overdue_provider: DataOverdueProvider
    overdue_handler: DataOverdueHandler
    routers: tuple[APIRouter, ...]
    providers: tuple[ScheduleProvider, ...]
    handlers: tuple[TaskHandler, ...]


def build_v14(parts: WiringV14Parts) -> WiringV14:
    """Build v1.4 inventory services and their API and worker registrations."""
    repository: InventoryRepository = InventoryRepository()
    observer: InventoryObserverImpl = InventoryObserverImpl(parts.collector_configs, repository)
    reconciler: InventoryReconcilerImpl = InventoryReconcilerImpl(
        parts.collector_configs, repository, parts.clock
    )
    queries: AssetQueries = AssetQueries(
        parts.issue_reader, InventoryActorDirectory(parts.actors, parts.collector_configs)
    )
    assets: AssetService = AssetService(repository, parts.audit, parts.redactor, parts.clock)
    fields: FieldService = FieldService(parts.audit, parts.redactor, parts.clock)
    validator: ValueValidator = ValueValidator(parts.recipients, parts.redactor)
    values: ValueService = ValueService(repository, validator, parts.audit, parts.clock)
    registration: RegistrationService = RegistrationService(
        repository, values, parts.fingerprinter, parts.redactor, parts.audit, parts.clock
    )
    owner_resolver: IssueAssetOwnerResolver = IssueAssetOwnerResolver(parts.owners)
    overdue_provider: DataOverdueProvider = DataOverdueProvider(
        parts.settings.data_overdue_cron or "", parts.queue
    )
    overdue_handler: DataOverdueHandler = DataOverdueHandler(
        parts.sessions,
        parts.members,
        parts.sender,
        parts.recipients,
        owner_resolver,
        parts.audit,
        parts.clock,
        parts.settings.public_base_url,
    )
    exporter: AssetExporter = AssetExporter(queries, parts.redactor)
    routers: tuple[APIRouter, ...] = (
        build_data_assets_router(
            DataAssetsRouterDeps(parts.context, queries, exporter, parts.audit, parts.redactor)
        ),
        build_data_asset_detail_router(DataAssetDetailRouterDeps(parts.context, queries, assets)),
        build_data_fields_router(DataFieldsRouterDeps(parts.context, fields, validator)),
        build_data_asset_values_router(
            DataAssetValuesRouterDeps(parts.context, values, registration, queries)
        ),
    )
    providers: tuple[ScheduleProvider, ...] = (overdue_provider,)
    handlers: tuple[TaskHandler, ...] = (overdue_handler,)
    return WiringV14(
        repository,
        observer,
        reconciler,
        queries,
        assets,
        fields,
        validator,
        values,
        registration,
        owner_resolver,
        overdue_provider,
        overdue_handler,
        routers,
        providers,
        handlers,
    )
