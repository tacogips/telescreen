"""Composition helpers for Telescreen v1.1 services and handlers."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telescreen.api.routers.collectors import CollectorHealthLister
from telescreen.collectors.coverage import CoverageRecorder
from telescreen.collectors.health import (
    CollectorHealthProvider,
    CollectorHealthRecorder,
    CollectorHealthView,
    list_health,
)
from telescreen.collectors.repository import CollectorRepository
from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.config import TelescreenSettings
from telescreen.core.vocabulary import AssetTier, RuleClass
from telescreen.db.models import CollectorConfigRow
from telescreen.detection import RuleDetector
from telescreen.directory.identities import IdentityService
from telescreen.directory.owners import DirectoryOwnerResolver
from telescreen.directory.resolver import DirectoryRecipientResolver
from telescreen.issues.digest import OwnerDigestHandler
from telescreen.issues.exceptions import ExceptionService
from telescreen.issues.lifecycle import IssueRemediationService
from telescreen.issues.maintenance import ExceptionMaintenanceProvider
from telescreen.issues.policy.decider import PlacementPolicyDecider
from telescreen.issues.policy.store import PolicyStore
from telescreen.issues.policy_apply import IssuePolicyMaintenance, PolicyReevaluateHandler
from telescreen.issues.risk import DefaultRiskScorer
from telescreen.issues.service import IssueService
from telescreen.issues.verification import VerifyRescanHandler
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.police.health import PoliceHealthProvider
from telescreen.ports.audit import AuditLog
from telescreen.ports.collectors import ResourceRescanner
from telescreen.ports.notifications import NotificationSender
from telescreen.ports.policy import RiskInputs, RiskScorer
from telescreen.ports.search import SearchIndexer
from telescreen.workflow.queue.sql_queue import SqlTaskQueue


@dataclass(frozen=True)
class PolicyRisk:
    """Policy storage, policy evaluation, and ordinal issue scoring."""

    store: PolicyStore
    decider: PlacementPolicyDecider
    scorer: DefaultRiskScorer
    issue_scorer: RiskScorer


@dataclass(frozen=True)
class UtcRiskScorer(RiskScorer):
    """Normalize SQLite timestamps before delegating to the default scorer."""

    scorer: DefaultRiskScorer

    def score(self, inputs: RiskInputs) -> int:
        """Score with timezone-aware timestamps while preserving default weights."""
        normalized: RiskInputs = RiskInputs(
            inputs.severity,
            inputs.visibility,
            inputs.asset_tier,
            ensure_utc(inputs.first_seen),
            ensure_utc(inputs.now),
        )
        return self.scorer.score(normalized)

    def asset_tier(self, rule_id: str, rule_class: RuleClass) -> AssetTier:
        """Delegate asset classification to the configured default scorer."""
        return self.scorer.asset_tier(rule_id, rule_class)


@dataclass(frozen=True)
class DirectoryV11:
    """Keyed member identity service and owner directory resolver."""

    identities: IdentityService
    owners: DirectoryOwnerResolver


@dataclass(frozen=True)
class IssueOps:
    """Issue lifecycle services, task handlers, and schedule providers."""

    remediation: IssueRemediationService
    exceptions: ExceptionService
    verify_handler: VerifyRescanHandler
    digest_handler: OwnerDigestHandler
    exception_maintenance: ExceptionMaintenanceProvider
    policy_handler: PolicyReevaluateHandler
    policy_maintenance: IssuePolicyMaintenance


@dataclass(frozen=True)
class Health:
    """Collector health recorders, providers, coverage, and API view lister."""

    collector_provider: CollectorHealthProvider
    police_provider: PoliceHealthProvider
    coverage: CoverageRecorder
    collector_recorder: CollectorHealthRecorder
    health_view: CollectorHealthLister


@dataclass(frozen=True)
class ConfiguredCollectorHealthLister(CollectorHealthLister):
    """Resolve collector health with configured failure and exposure thresholds."""

    failure_threshold: int
    alert_unscannable_public: bool

    async def list(
        self,
        session: AsyncSession,
        configs: tuple[CollectorConfigRow, ...],
        now: datetime,
    ) -> tuple[tuple[str, CollectorHealthView], ...]:
        """Return safe health views for the configured collector rows."""
        return await list_health(
            session,
            configs,
            now,
            self.failure_threshold,
            self.alert_unscannable_public,
        )


def build_policy_and_risk(
    settings: TelescreenSettings,
    redactor: Redactor,
    audit: AuditLog,
    fingerprinter: Fingerprinter,
    queue: SqlTaskQueue,
    resolver: DirectoryRecipientResolver,
    clock: Clock,
) -> PolicyRisk:
    """Build versioned policy storage, evaluator, and risk scorer."""
    store: PolicyStore = PolicyStore(redactor, RuleDetector(), audit, queue, resolver, clock)
    decider: PlacementPolicyDecider = PlacementPolicyDecider(fingerprinter, settings.owner_routing)
    scorer: DefaultRiskScorer = DefaultRiskScorer(settings.risk_weights)
    issue_scorer: UtcRiskScorer = UtcRiskScorer(scorer)
    return PolicyRisk(store, decider, scorer, issue_scorer)


def build_directory_v11(
    fingerprinter: Fingerprinter, audit: AuditLog, clock: Clock
) -> DirectoryV11:
    """Build identity management and keyed owner resolution."""
    return DirectoryV11(IdentityService(fingerprinter, audit, clock), DirectoryOwnerResolver())


def build_issue_ops(
    settings: TelescreenSettings,
    sessions: async_sessionmaker[AsyncSession],
    audit: AuditLog,
    clock: Clock,
    redactor: Redactor,
    queue: SqlTaskQueue,
    resolver: DirectoryRecipientResolver,
    owners: DirectoryOwnerResolver,
    decider: PlacementPolicyDecider,
    issue_service: IssueService,
    rescanner: ResourceRescanner,
    notifications: NotificationSender,
    indexer: SearchIndexer,
) -> IssueOps:
    """Build remediation, exception, verification, digest, and policy operations."""
    exceptions: ExceptionService = ExceptionService(
        audit,
        clock,
        redactor,
        owners,
        resolver,
        decider,
        settings.default_team,
        notifications,
    )
    remediation: IssueRemediationService = IssueRemediationService(
        audit,
        clock,
        redactor,
        queue,
        resolver,
        owners,
        decider,
        exceptions,
        settings.default_team,
    )
    verify_handler: VerifyRescanHandler = VerifyRescanHandler(
        sessions, rescanner, audit, clock, owners, resolver, notifications
    )
    digest_handler: OwnerDigestHandler = OwnerDigestHandler(
        sessions, owners, resolver, notifications, clock
    )
    exception_maintenance: ExceptionMaintenanceProvider = ExceptionMaintenanceProvider(
        exceptions, queue, settings.digest_cron
    )
    policy_handler: PolicyReevaluateHandler = PolicyReevaluateHandler(
        sessions, issue_service, indexer, clock
    )
    policy_maintenance: IssuePolicyMaintenance = IssuePolicyMaintenance(issue_service)
    return IssueOps(
        remediation,
        exceptions,
        verify_handler,
        digest_handler,
        exception_maintenance,
        policy_handler,
        policy_maintenance,
    )


def build_health(
    settings: TelescreenSettings,
    collector_repository: CollectorRepository,
    audit: AuditLog,
    clock: Clock,
    notifications: NotificationSender,
) -> Health:
    """Build collector and Police health providers and coverage recorders."""
    coverage: CoverageRecorder = CoverageRecorder()
    collector_recorder: CollectorHealthRecorder = CollectorHealthRecorder()
    collector_provider: CollectorHealthProvider = CollectorHealthProvider(
        collector_repository,
        coverage,
        audit,
        notifications,
        settings.health_recipients,
        settings.health_failure_threshold,
        settings.alert_unscannable_public,
    )
    police_provider: PoliceHealthProvider = PoliceHealthProvider(
        audit, clock, notifications, settings.health_recipients
    )
    health_view: ConfiguredCollectorHealthLister = ConfiguredCollectorHealthLister(
        settings.health_failure_threshold, settings.alert_unscannable_public
    )
    return Health(collector_provider, police_provider, coverage, collector_recorder, health_view)
