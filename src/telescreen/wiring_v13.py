"""Composition helpers for v1.3 issue, notification, and organization services."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.alerts.evaluator import AlertEvaluator
from telescreen.alerts.maintenance import AlertMaintenanceProvider
from telescreen.alerts.repository import AlertRepository
from telescreen.alerts.service import AlertRuleService
from telescreen.api.common.context import ApiContext
from telescreen.api.routers.alerts import AlertsRouterDeps, build_alerts_router
from telescreen.api.routers.dashboard import DashboardRouterDeps, build_dashboard_router
from telescreen.api.routers.integrations import (
    IntegrationsRouterDeps,
    build_integrations_router,
)
from telescreen.api.routers.issue_activity import build_issue_activity_router
from telescreen.api.routers.issue_bulk import IssueBulkRouterDeps, build_issue_bulk_router
from telescreen.api.routers.issue_counts import IssueCountsRouterDeps, build_issue_counts_router
from telescreen.api.routers.issue_events import build_issue_events_router
from telescreen.api.routers.issue_external import (
    IssueExternalRouterDeps,
    build_issue_external_router,
)
from telescreen.api.routers.issue_triage import IssueTriageRouterDeps, build_issue_triage_router
from telescreen.api.routers.issue_views import IssueViewsRouterDeps, build_issue_views_router
from telescreen.api.routers.issues import build_issues_router
from telescreen.api.routers.issues_common import IssuesRouterDeps
from telescreen.api.routers.ownership import OwnershipRouterDeps, build_ownership_router
from telescreen.core.config import TelescreenSettings
from telescreen.core.secrets import EnvSource
from telescreen.directory.actors import DirectoryActorDirectory
from telescreen.directory.members import MemberAdminService
from telescreen.directory.repository import DirectoryRepository
from telescreen.integrations.repository import IntegrationCheckRepository
from telescreen.integrations.status import ChannelStatusSource, IntegrationStatusService
from telescreen.integrations.tests import IntegrationTester
from telescreen.issues.activity.notifier import SubscriptionActivityNotifier
from telescreen.issues.archive import ArchiveMaintenanceProvider, ArchivePolicy
from telescreen.issues.bulk import BulkService
from telescreen.issues.dashboard import DashboardService, IssueReportSourceImpl
from telescreen.issues.external.github import GithubIssueCreator
from telescreen.issues.external.links import ExternalLinkService
from telescreen.issues.merge import MergeService
from telescreen.issues.ownership import OwnershipAssigneeSuggester, OwnershipRuleService
from telescreen.issues.service import IssueService
from telescreen.issues.views import SavedViewService
from telescreen.notifications.dlp import DlpGate
from telescreen.notifications.guard import DlpOutboundGuard
from telescreen.ports.directory import (
    ActorDirectory,
    OwnerDirectory,
    RecipientResolver,
)
from telescreen.ports.issues import AssigneeSuggester, IssueEvent, IssueEventSink
from telescreen.ports.notifications import MemberNotifier, NotificationSender, NotificationTester
from telescreen.ports.workflow import ScheduleProvider, TaskHandler
from telescreen.wiring_builders import RuntimePrimitives
from telescreen.wiring_v11 import IssueOps
from telescreen.wiring_v12 import WiringV12Parts
from telescreen.workflow.engine.engine import RunEngine
from telescreen.workflow.engine.repository import WorkflowRepository
from telescreen.workflow.queue.sql_queue import SqlTaskQueue
from telescreen.workflow.triggers.service import WorkflowTriggerService
from telescreen.workflow.triggers.starter import WorkflowRunStarter


@dataclass(frozen=True)
class IssueEventFanout(IssueEventSink):
    """Deliver issue events to each configured sink in order."""

    sinks: tuple[IssueEventSink, ...]

    async def on_issue_event(self, session: AsyncSession, event: IssueEvent) -> None:
        """Forward an event sequentially, propagating the first sink failure."""
        for sink in self.sinks:
            await sink.on_issue_event(session, event)


@dataclass(frozen=True)
class WiringV13Parts:
    """Existing composition-root dependencies needed to construct v1.3 wiring."""

    settings: TelescreenSettings
    env: EnvSource
    http: httpx.AsyncClient
    context: ApiContext
    primitives: RuntimePrimitives
    resolver: RecipientResolver
    owners: OwnerDirectory
    notification_members: MemberNotifier
    issue_service: IssueService
    issue_ops: IssueOps
    notification_service: NotificationSender
    notification_gate: DlpGate
    notification_tester: NotificationTester
    channel_status: ChannelStatusSource | None
    queue: SqlTaskQueue
    workflow_triggers: WorkflowTriggerService
    workflow_repository: WorkflowRepository
    run_engine: RunEngine
    v12: WiringV12Parts


@dataclass(frozen=True)
class WiringV13:
    """Built v1.3 services and the routers, providers, and handlers they expose."""

    actors: ActorDirectory
    suggester: AssigneeSuggester
    activity: SubscriptionActivityNotifier
    alert_evaluator: AlertEvaluator
    archive_policy: ArchivePolicy
    member_admin: MemberAdminService
    issues_router_deps: IssuesRouterDeps
    routers: tuple[APIRouter, ...]
    providers: tuple[ScheduleProvider, ...]
    handlers: tuple[TaskHandler, ...]


def build_v13(parts: WiringV13Parts) -> WiringV13:
    """Build v1.3 services and routers from shared application primitives."""
    primitives: RuntimePrimitives = parts.primitives
    settings: TelescreenSettings = parts.settings
    actors: DirectoryActorDirectory = DirectoryActorDirectory()
    activity: SubscriptionActivityNotifier = SubscriptionActivityNotifier(
        parts.notification_members, actors, primitives.clock
    )
    archive_policy: ArchivePolicy = ArchivePolicy(
        settings.escalating_min_findings, settings.escalating_factor
    )
    starter: WorkflowRunStarter = WorkflowRunStarter(parts.run_engine, parts.workflow_repository)
    alert_repository: AlertRepository = AlertRepository()
    alert_evaluator: AlertEvaluator = AlertEvaluator(
        parts.notification_service, starter, parts.issue_service, primitives.clock, alert_repository
    )
    parts.issue_service.findings_observer = alert_evaluator
    parts.issue_service.activity = activity
    parts.issue_service.archive_policy = archive_policy

    views: SavedViewService = SavedViewService(
        primitives.redactor, primitives.audit, primitives.clock
    )
    bulk: BulkService = BulkService(
        primitives.sessions,
        parts.issue_service,
        parts.issue_ops.remediation,
        archive_policy,
        primitives.clock,
        primitives.audit,
        primitives.indexer,
    )
    merge: MergeService = MergeService(
        primitives.sessions, primitives.audit, primitives.clock, primitives.indexer, activity
    )
    alert_service: AlertRuleService = AlertRuleService(
        primitives.redactor,
        primitives.audit,
        primitives.clock,
        parts.v12.templates,
        starter,
        alert_repository,
    )
    from telescreen.issues.weekly_report import WeeklyReportHandler, WeeklyReportProvider

    report_source: IssueReportSourceImpl = IssueReportSourceImpl()
    weekly_provider: WeeklyReportProvider = WeeklyReportProvider(
        settings.weekly_report_cron, parts.queue
    )
    weekly_handler: WeeklyReportHandler = WeeklyReportHandler(
        primitives.sessions, report_source, parts.notification_members, primitives.clock
    )
    checks: IntegrationCheckRepository = IntegrationCheckRepository()
    integration_tester: IntegrationTester = IntegrationTester(
        parts.notification_tester,
        parts.v12.verifier,
        parts.http._transport,
        settings,
        parts.env,
        checks,
        primitives.audit,
        primitives.clock,
        channels=parts.channel_status,
    )
    github: GithubIssueCreator | None = None
    if settings.github_issues_token_ref is not None:
        github = GithubIssueCreator(
            parts.http._transport,
            settings.github_api_url,
            settings.github_issues_token_ref,
            settings.github_issues_repos,
            parts.env,
            DlpOutboundGuard(parts.notification_gate),
            primitives.redactor,
            primitives.audit,
            primitives.clock,
            settings.public_base_url,
        )
    ownership: OwnershipRuleService = OwnershipRuleService(
        primitives.redactor, primitives.audit, primitives.clock
    )
    suggester: OwnershipAssigneeSuggester = OwnershipAssigneeSuggester(parts.owners)
    member_admin: MemberAdminService = MemberAdminService(
        DirectoryRepository(),
        parts.v12.roles,
        parts.issue_ops.remediation,
        primitives.audit,
        primitives.clock,
        primitives.redactor,
    )
    issues_deps: IssuesRouterDeps = IssuesRouterDeps(
        parts.context,
        parts.issue_service,
        primitives.indexer,
        parts.issue_ops.remediation,
        parts.issue_ops.exceptions,
        parts.owners,
        parts.resolver,
        actors,
        suggester,
        parts.notification_members,
    )
    archive_provider: ArchiveMaintenanceProvider = ArchiveMaintenanceProvider(
        primitives.audit, activity
    )
    alert_provider: AlertMaintenanceProvider = AlertMaintenanceProvider(
        alert_repository, primitives.clock
    )
    routers: tuple[APIRouter, ...] = (
        build_issue_counts_router(
            IssueCountsRouterDeps(parts.context, parts.resolver, parts.owners)
        ),
        build_issue_views_router(IssueViewsRouterDeps(parts.context, views)),
        build_issue_triage_router(
            IssueTriageRouterDeps(parts.context, parts.issue_service, primitives.indexer)
        ),
        build_issue_bulk_router(
            IssueBulkRouterDeps(
                parts.context,
                bulk,
                merge,
                primitives.indexer,
                parts.resolver,
                parts.owners,
                archive_policy,
                activity,
            )
        ),
        build_issue_events_router(issues_deps),
        build_issue_activity_router(issues_deps),
        build_issue_external_router(
            IssueExternalRouterDeps(
                parts.context,
                ExternalLinkService(primitives.redactor, primitives.audit, primitives.clock),
                github,
                settings.github_issues_repos,
            )
        ),
        build_alerts_router(AlertsRouterDeps(parts.context, alert_service)),
        build_dashboard_router(DashboardRouterDeps(parts.context, DashboardService())),
        build_integrations_router(
            IntegrationsRouterDeps(
                parts.context,
                IntegrationStatusService(settings, checks, parts.channel_status),
                integration_tester,
                checks,
            )
        ),
        build_ownership_router(OwnershipRouterDeps(parts.context, ownership)),
        build_issues_router(issues_deps),
    )
    providers: tuple[ScheduleProvider, ...] = (archive_provider, alert_provider, weekly_provider)
    handlers: tuple[TaskHandler, ...] = (weekly_handler,)
    return WiringV13(
        actors,
        suggester,
        activity,
        alert_evaluator,
        archive_policy,
        member_admin,
        issues_deps,
        routers,
        providers,
        handlers,
    )
