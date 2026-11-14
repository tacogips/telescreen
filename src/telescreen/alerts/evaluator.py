"""Evaluate alert rules on masked issue lifecycle and finding events."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.alerts.model import AlertActions, AlertFilters, actions_from_json, filters_from_json
from telescreen.alerts.repository import AlertRepository
from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import JsonValue, parse_json
from telescreen.core.vocabulary import (
    AlertFireOutcome,
    AlertTriggerKind,
    DlpVerdict,
    IssueEventKind,
    IssueStatus,
    NotificationCategory,
    NotificationEvent,
    RunTrigger,
    Severity,
)
from telescreen.db.models import AlertFireRow, AlertRuleRow, FindingRow, IssueRow
from telescreen.ports.issues import (
    FindingsObserver,
    FindingsRecorded,
    IssueEvent,
    IssueEventSink,
    IssueReader,
    IssueSnapshot,
)
from telescreen.ports.notifications import (
    NotificationContext,
    NotificationReceipt,
    NotificationRequest,
    NotificationSender,
)
from telescreen.ports.workflow import RunStarter


@dataclass
class AlertEvaluator(IssueEventSink, FindingsObserver):
    """Evaluate rules and persist fire outcomes inside ingest transactions."""

    sender: NotificationSender
    starter: RunStarter
    issues: IssueReader
    clock: Clock
    repository: AlertRepository

    async def on_issue_event(self, session: AsyncSession, event: IssueEvent) -> None:
        """Evaluate event-triggered rules for one issue lifecycle event."""
        issue_row: IssueRow | None = await session.get(IssueRow, event.issue_id)
        if issue_row is None:
            return
        primary_id: str = issue_row.merged_into_id or issue_row.id
        trigger: AlertTriggerKind = AlertTriggerKind(event.kind.value)
        await self._evaluate(session, primary_id, trigger, event.kind, None)

    async def on_findings_recorded(self, session: AsyncSession, event: FindingsRecorded) -> None:
        """Evaluate frequency and distinct-value rules after findings are stored."""
        issue_row: IssueRow | None = await session.get(IssueRow, event.issue_id)
        if issue_row is None:
            return
        primary_id: str = issue_row.merged_into_id or issue_row.id
        primary: IssueRow | None = await session.get(IssueRow, primary_id)
        if primary is None or primary.status in (
            IssueStatus.ARCHIVED.value,
            IssueStatus.IGNORED.value,
        ):
            return
        await self._evaluate(session, primary_id, AlertTriggerKind.FREQUENCY, None, event)
        await self._evaluate(session, primary_id, AlertTriggerKind.VALUES, None, event)

    async def _evaluate(
        self,
        session: AsyncSession,
        issue_id: str,
        trigger: AlertTriggerKind,
        event_kind: IssueEventKind | None,
        finding_event: FindingsRecorded | None,
    ) -> None:
        """Load matching rules and execute their actions in the caller transaction."""
        snapshot: IssueSnapshot | None = await self.issues.get(session, issue_id)
        if snapshot is None:
            return
        row: IssueRow | None = await session.get(IssueRow, issue_id)
        if row is None or row.merged_into_id is not None:
            return
        rules: tuple[AlertRuleRow, ...] = await self.repository.list_rules(session)
        now: datetime = self.clock.now()
        for rule in rules:
            if not rule.enabled or rule.trigger_kind != trigger.value:
                continue
            count: int = 1
            if trigger in (AlertTriggerKind.FREQUENCY, AlertTriggerKind.VALUES):
                if row.status in (IssueStatus.ARCHIVED.value, IssueStatus.IGNORED.value):
                    continue
                count = await self._finding_count(session, issue_id, rule, trigger)
                if rule.threshold is None or count < rule.threshold:
                    continue
            filters: AlertFilters = filters_from_json(parse_json(rule.filters_json))
            if not _matches(filters, snapshot):
                continue
            if filters.detectors and not await self._has_detector(session, issue_id, filters):
                continue
            if rule.muted_until is not None and ensure_utc(rule.muted_until) > now:
                latest_muted: AlertFireRow | None = await self.repository.latest_muted_issue_fire(
                    session, rule.id, issue_id
                )
                if (
                    latest_muted is not None
                    and ensure_utc(latest_muted.fired_at)
                    + timedelta(minutes=rule.action_interval_minutes)
                    > now
                ):
                    continue
                await self._fire(
                    session, rule, issue_id, trigger, AlertFireOutcome.MUTED, 0, None, None
                )
                continue
            latest_delivered: (
                AlertFireRow | None
            ) = await self.repository.latest_delivered_issue_fire(session, rule.id, issue_id)
            inside_interval: bool = (
                latest_delivered is not None
                and ensure_utc(latest_delivered.fired_at)
                + timedelta(minutes=rule.action_interval_minutes)
                > now
            )
            if inside_interval and latest_delivered is not None:
                suppressed_this_window: bool = await self.repository.has_suppressed_interval_since(
                    session, rule.id, issue_id, ensure_utc(latest_delivered.fired_at)
                )
                if suppressed_this_window:
                    continue
                await self._fire(
                    session,
                    rule,
                    issue_id,
                    trigger,
                    AlertFireOutcome.SUPPRESSED_INTERVAL,
                    0,
                    None,
                    None,
                )
                continue
            await self._act(session, rule, snapshot, trigger, count, event_kind, finding_event)

    async def _finding_count(
        self, session: AsyncSession, issue_id: str, rule: AlertRuleRow, trigger: AlertTriggerKind
    ) -> int:
        """Count recent findings or distinct fingerprints over the rule window."""
        cutoff: datetime = self.clock.now() - timedelta(minutes=rule.window_minutes or 5)
        group_ids: tuple[str, ...] = tuple(
            (
                await session.scalars(
                    select(IssueRow.id).where(
                        (IssueRow.id == issue_id) | (IssueRow.merged_into_id == issue_id)
                    )
                )
            ).all()
        )
        if trigger is AlertTriggerKind.FREQUENCY:
            value: int | None = await session.scalar(
                select(func.count(FindingRow.id)).where(
                    FindingRow.issue_id.in_(group_ids), FindingRow.observed_at >= cutoff
                )
            )
        else:
            value = await session.scalar(
                select(func.count(func.distinct(FindingRow.fingerprint))).where(
                    FindingRow.issue_id.in_(group_ids), FindingRow.observed_at >= cutoff
                )
            )
        return value or 0

    async def _has_detector(
        self, session: AsyncSession, issue_id: str, filters: AlertFilters
    ) -> bool:
        """Check whether a group's findings use one of the requested detectors."""
        group_ids: tuple[str, ...] = tuple(
            (
                await session.scalars(
                    select(IssueRow.id).where(
                        (IssueRow.id == issue_id) | (IssueRow.merged_into_id == issue_id)
                    )
                )
            ).all()
        )
        finding_id: str | None = await session.scalar(
            select(FindingRow.id)
            .where(
                FindingRow.issue_id.in_(group_ids),
                FindingRow.detector.in_(filters.detectors),
            )
            .limit(1)
        )
        return finding_id is not None

    async def _act(
        self,
        session: AsyncSession,
        rule: AlertRuleRow,
        snapshot: IssueSnapshot,
        trigger: AlertTriggerKind,
        count: int,
        event_kind: IssueEventKind | None,
        finding_event: FindingsRecorded | None,
    ) -> None:
        """Queue target notifications and start the optional workflow action."""
        actions: AlertActions = _actions(parse_json(rule.actions_json))
        request_count: int = 0
        all_blocked: bool = bool(actions.targets)
        run_id: str | None = None
        failure: TelescreenError | None = None
        for target_index, target in enumerate(actions.targets):
            for channel in target.channels:
                request: NotificationRequest = NotificationRequest(
                    channel=channel,
                    recipients=target.recipients,
                    subject="",
                    body="",
                    actor_ref="system:alerts",
                    issue_id=snapshot.id,
                    event=NotificationEvent.ALERT_FIRED,
                    template_name=target.template,
                    context=NotificationContext(
                        alert_rule=rule.name,
                        alert_trigger=trigger.value,
                        alert_count=str(count),
                        alert_window=str(rule.window_minutes or 0),
                        issue_short_id=snapshot.short_id,
                        issue_title=snapshot.display_title,
                        issue_rule_class=snapshot.rule_class.value,
                        issue_severity=snapshot.severity.value,
                        issue_status=snapshot.status.value,
                        issue_location=snapshot.display_locator,
                        issue_excerpt=snapshot.masked_excerpt,
                    ),
                    target_index=target_index,
                    record_unreachable=True,
                    category=NotificationCategory.ALERTS,
                )
                try:
                    receipt: NotificationReceipt = await self.sender.send(session, request)
                    request_count += len(receipt.notification_ids) if receipt.queued else 0
                    all_blocked = all_blocked and receipt.verdict is DlpVerdict.BLOCKED
                except TelescreenError as error:
                    failure = error
                    break
            if failure is not None:
                break
        if failure is None and actions.start_workflow is not None:
            try:
                run_id = await self.starter.start_for_issue(
                    session, actions.start_workflow, snapshot.id, RunTrigger.ALERT_RULE
                )
            except TelescreenError as error:
                failure = error
        outcome: AlertFireOutcome
        error_code: str | None = None
        if failure is not None:
            outcome = AlertFireOutcome.FAILED
            error_code = failure.code.value
        elif request_count > 0 or run_id is not None:
            outcome = AlertFireOutcome.DELIVERED
        elif all_blocked:
            outcome = AlertFireOutcome.BLOCKED
        else:
            outcome = AlertFireOutcome.FAILED
            error_code = "no_action_started"
        await self._fire(
            session, rule, snapshot.id, trigger, outcome, request_count, run_id, error_code
        )
        if outcome is AlertFireOutcome.DELIVERED:
            rule.last_fired_at = self.clock.now()

    async def _fire(
        self,
        session: AsyncSession,
        rule: AlertRuleRow,
        issue_id: str,
        trigger: AlertTriggerKind,
        outcome: AlertFireOutcome,
        notification_count: int,
        run_id: str | None,
        error_code: str | None,
    ) -> None:
        """Persist a fire row with safe outcome metadata."""
        await self.repository.add_fire(
            session,
            AlertFireRow(
                id=new_id(),
                rule_id=rule.id,
                issue_id=issue_id,
                trigger_kind=trigger.value,
                outcome=outcome.value,
                notification_count=notification_count,
                run_id=run_id,
                error_code=error_code,
                fired_at=self.clock.now(),
            ),
        )


def _matches(filters: AlertFilters, issue: IssueSnapshot) -> bool:
    """Apply configured filters to masked issue metadata."""
    if filters.min_severity is not None and _severity_rank(issue.severity) < _severity_rank(
        filters.min_severity
    ):
        return False
    if filters.priorities and issue.priority not in filters.priorities:
        return False
    if filters.sources and issue.source not in filters.sources:
        return False
    if filters.rule_classes and issue.rule_class.value not in filters.rule_classes:
        return False
    if filters.exposures and issue.visibility.value not in filters.exposures:
        return False
    return not (filters.owner_teams and issue.assignee_team_slug not in filters.owner_teams)


def _severity_rank(severity: Severity) -> int:
    """Return the ordering rank used by severity filters."""
    return {Severity.CRITICAL: 3, Severity.HIGH: 2, Severity.MEDIUM: 1}[severity]


def _actions(value: JsonValue) -> AlertActions:
    """Decode persisted notification target and workflow action data."""
    return actions_from_json(value)
