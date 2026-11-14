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
from telescreen.audit.receiver import AuditCommandReceiver
from telescreen.audit.service import AuditLogService
from telescreen.collectors.inventory_publish import InventoryPublisher
from telescreen.core.clock import Clock
from telescreen.core.config import TelescreenSettings
from telescreen.directory.inventory_actors import InventoryActorDirectory
from telescreen.directory.inventory_context import IdentityActorLookup, IdentityAssigneeLookup
from telescreen.integrations.inventory_audit_delivery import (
    InventoryAuditDeliveryHandler,
    InventoryAuditRecoveryProvider,
)
from telescreen.integrations.inventory_delivery import (
    InventoryDeliveryHandler,
    InventoryRecoveryProvider,
)
from telescreen.integrations.inventory_outbox import InventoryOutbox
from telescreen.integrations.retention_delivery import (
    RetentionDeliveryHandler,
    RetentionRecoveryProvider,
)
from telescreen.inventory.assets import AssetService
from telescreen.inventory.audit import InventoryAuditOutbox, InventoryAuditPublisher
from telescreen.inventory.export import AssetExporter
from telescreen.inventory.fields import FieldService
from telescreen.inventory.handoff.apply import InventoryOperationApplier
from telescreen.inventory.handoff.inbox import InventoryInbox
from telescreen.inventory.observer import InventoryObserverImpl
from telescreen.inventory.queries import AssetQueries
from telescreen.inventory.reconcile import InventoryReconcilerImpl
from telescreen.inventory.registration import RegistrationService
from telescreen.inventory.repository import InventoryRepository
from telescreen.inventory.retention import DataOverdueHandler, DataOverdueProvider
from telescreen.inventory.retention_outbox import InventoryRetentionLookup, RetentionOutbox
from telescreen.inventory.values import ValueService, ValueValidator
from telescreen.issues.asset_context import IssueAssetLookup, IssueAssetOwnerLookup
from telescreen.issues.asset_owner import IssueAssetOwnerResolver
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.notifications.retention import RetentionNotificationReceiver
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
    inventory_sessions: async_sessionmaker[AsyncSession] | None = None


@dataclass(frozen=True)
class WiringV14:
    """Built v1.4 services, inventory hooks, and runtime registrations."""

    repository: InventoryRepository
    observer: InventoryObserverImpl
    reconciler: InventoryReconcilerImpl
    publisher: InventoryPublisher
    inbox: InventoryInbox
    delivery_handler: InventoryDeliveryHandler
    recovery_provider: InventoryRecoveryProvider
    audit_publisher: InventoryAuditPublisher
    audit_delivery_handler: InventoryAuditDeliveryHandler
    audit_recovery_provider: InventoryAuditRecoveryProvider
    queries: AssetQueries
    assets: AssetService
    fields: FieldService
    validator: ValueValidator
    values: ValueService
    registration: RegistrationService
    owner_resolver: IssueAssetOwnerResolver
    overdue_provider: DataOverdueProvider
    overdue_handler: DataOverdueHandler
    retention_receiver: RetentionNotificationReceiver
    retention_delivery_handler: RetentionDeliveryHandler
    retention_recovery_provider: RetentionRecoveryProvider
    routers: tuple[APIRouter, ...]
    providers: tuple[ScheduleProvider, ...]
    handlers: tuple[TaskHandler, ...]


def build_v14(parts: WiringV14Parts) -> WiringV14:
    """Build v1.4 inventory services and their API and worker registrations."""
    inventory_sessions: async_sessionmaker[AsyncSession] = (
        parts.inventory_sessions or parts.sessions
    )
    repository: InventoryRepository = InventoryRepository()
    observer: InventoryObserverImpl = InventoryObserverImpl(repository)
    reconciler: InventoryReconcilerImpl = InventoryReconcilerImpl(repository, parts.clock)
    outbox: InventoryOutbox = InventoryOutbox()
    publisher: InventoryPublisher = InventoryPublisher(outbox, parts.queue, parts.clock)
    inbox: InventoryInbox = InventoryInbox(
        inventory_sessions, InventoryOperationApplier(observer, reconciler), parts.clock
    )
    delivery_handler: InventoryDeliveryHandler = InventoryDeliveryHandler(
        parts.sessions, outbox, inbox, parts.clock
    )
    recovery_provider: InventoryRecoveryProvider = InventoryRecoveryProvider(outbox, parts.queue)
    audit_outbox: InventoryAuditOutbox = InventoryAuditOutbox()
    audit_publisher: InventoryAuditPublisher = InventoryAuditPublisher(parts.clock, parts.redactor)
    audit_delivery_handler: InventoryAuditDeliveryHandler = InventoryAuditDeliveryHandler(
        inventory_sessions,
        audit_outbox,
        AuditCommandReceiver(parts.sessions, AuditLogService(parts.clock, parts.redactor)),
        parts.clock,
    )
    audit_recovery_provider: InventoryAuditRecoveryProvider = InventoryAuditRecoveryProvider(
        audit_outbox,
        parts.queue,
        source_sessions=inventory_sessions if inventory_sessions is not parts.sessions else None,
    )
    queries: AssetQueries = AssetQueries(
        IssueAssetLookup(parts.sessions, parts.issue_reader),
        IdentityActorLookup(
            parts.sessions, InventoryActorDirectory(parts.actors, parts.collector_configs)
        ),
    )
    assets: AssetService = AssetService(repository, audit_publisher, parts.redactor, parts.clock)
    fields: FieldService = FieldService(audit_publisher, parts.redactor, parts.clock)
    validator: ValueValidator = ValueValidator(
        IdentityAssigneeLookup(parts.sessions, parts.recipients), parts.redactor
    )
    values: ValueService = ValueService(repository, validator, audit_publisher, parts.clock)
    registration: RegistrationService = RegistrationService(
        repository, values, parts.fingerprinter, parts.redactor, audit_publisher, parts.clock
    )
    owner_resolver: IssueAssetOwnerResolver = IssueAssetOwnerResolver(parts.owners)
    overdue_provider: DataOverdueProvider = DataOverdueProvider(
        parts.settings.data_overdue_cron or "", parts.queue
    )
    overdue_handler: DataOverdueHandler = DataOverdueHandler(
        inventory_sessions, parts.redactor, parts.clock, parts.settings.public_base_url
    )
    retention_receiver: RetentionNotificationReceiver = RetentionNotificationReceiver(
        parts.sessions,
        InventoryRetentionLookup(inventory_sessions, parts.clock),
        parts.members,
        parts.sender,
        parts.recipients,
        IssueAssetOwnerLookup(parts.sessions, owner_resolver),
        AuditLogService(parts.clock, parts.redactor),
        parts.clock,
    )
    retention_outbox: RetentionOutbox = RetentionOutbox()
    retention_delivery_handler: RetentionDeliveryHandler = RetentionDeliveryHandler(
        inventory_sessions, retention_outbox, retention_receiver, parts.clock
    )
    retention_recovery_provider: RetentionRecoveryProvider = RetentionRecoveryProvider(
        retention_outbox,
        parts.queue,
        source_sessions=inventory_sessions if inventory_sessions is not parts.sessions else None,
    )
    exporter: AssetExporter = AssetExporter(queries, parts.redactor)
    routers: tuple[APIRouter, ...] = (
        build_data_assets_router(
            DataAssetsRouterDeps(
                parts.context,
                queries,
                exporter,
                audit_publisher,
                parts.redactor,
                inventory_sessions=inventory_sessions,
            )
        ),
        build_data_asset_detail_router(
            DataAssetDetailRouterDeps(
                parts.context, queries, assets, inventory_sessions=inventory_sessions
            )
        ),
        build_data_fields_router(
            DataFieldsRouterDeps(
                parts.context, fields, validator, inventory_sessions=inventory_sessions
            )
        ),
        build_data_asset_values_router(
            DataAssetValuesRouterDeps(
                parts.context, values, registration, queries, inventory_sessions=inventory_sessions
            )
        ),
    )
    providers: tuple[ScheduleProvider, ...] = (
        recovery_provider,
        audit_recovery_provider,
        overdue_provider,
        retention_recovery_provider,
    )
    handlers: tuple[TaskHandler, ...] = (
        delivery_handler,
        audit_delivery_handler,
        overdue_handler,
        retention_delivery_handler,
    )
    return WiringV14(
        repository,
        observer,
        reconciler,
        publisher,
        inbox,
        delivery_handler,
        recovery_provider,
        audit_publisher,
        audit_delivery_handler,
        audit_recovery_provider,
        queries,
        assets,
        fields,
        validator,
        values,
        registration,
        owner_resolver,
        overdue_provider,
        overdue_handler,
        retention_receiver,
        retention_delivery_handler,
        retention_recovery_provider,
        routers,
        providers,
        handlers,
    )
