"""Alert rule lifecycle, safe validation, and audit records."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.alerts.model import AlertRule, actions_json, filters_json
from telescreen.alerts.repository import AlertRepository
from telescreen.alerts.validate import RuleIssue, validate_rule
from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import dump_json, json_object, json_str
from telescreen.core.vocabulary import ActorKind, ErrorCode
from telescreen.db.models import AlertFireRow, AlertRuleRow
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.templates import TemplateCatalog
from telescreen.ports.workflow import RunStarter


@dataclass
class AlertRuleService:
    """Manage alert rules and history in caller-owned transactions."""

    redactor: Redactor
    audit: AuditLog
    clock: Clock
    catalog: TemplateCatalog | None
    starter: RunStarter | None
    repository: AlertRepository

    async def validate(self, session: AsyncSession, rule: AlertRule) -> tuple[RuleIssue, ...]:
        """Validate a rule without persisting it."""
        return await validate_rule(
            rule, self.redactor, self.clock, self.catalog, self.starter, session
        )

    async def list(self, session: AsyncSession) -> tuple[AlertRuleRow, ...]:
        """List alert rules in stable name order."""
        return await self.repository.list_rules(session)

    async def get(self, session: AsyncSession, rule_id: str) -> AlertRuleRow:
        """Get a rule or raise a safe not-found error."""
        row: AlertRuleRow | None = await self.repository.get_rule(session, rule_id)
        if row is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "alert rule not found")
        return row

    async def create(self, session: AsyncSession, rule: AlertRule, actor_ref: str) -> AlertRuleRow:
        """Validate and persist a rule, rejecting duplicate names without echoing them."""
        issues: tuple[RuleIssue, ...] = await self.validate(session, rule)
        if issues:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid alert rule")
        now: datetime = self.clock.now()
        row: AlertRuleRow = AlertRuleRow(
            id=new_id(),
            name=rule.name,
            enabled=True,
            trigger_kind=rule.trigger.kind.value,
            threshold=rule.trigger.threshold,
            window_minutes=rule.trigger.window_minutes,
            filters_json=dump_json(filters_json(rule.filters)),
            actions_json=dump_json(actions_json(rule.actions)),
            action_interval_minutes=rule.action_interval_minutes,
            muted_until=None,
            last_fired_at=None,
            created_by=actor_ref,
            created_at=now,
            updated_at=now,
        )
        try:
            await self.repository.insert_rule(session, row)
        except IntegrityError:
            raise TelescreenError(ErrorCode.CONFLICT, "alert rule already exists") from None
        await self._audit(session, actor_ref, "alert_rule.created", row)
        return row

    async def update(
        self, session: AsyncSession, rule_id: str, rule: AlertRule, actor_ref: str
    ) -> AlertRuleRow:
        """Validate and replace mutable rule configuration."""
        row: AlertRuleRow = await self.get(session, rule_id)
        issues: tuple[RuleIssue, ...] = await self.validate(session, rule)
        if issues:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid alert rule")
        row.name = rule.name
        row.trigger_kind = rule.trigger.kind.value
        row.threshold = rule.trigger.threshold
        row.window_minutes = rule.trigger.window_minutes
        row.filters_json = dump_json(filters_json(rule.filters))
        row.actions_json = dump_json(actions_json(rule.actions))
        row.action_interval_minutes = rule.action_interval_minutes
        row.updated_at = self.clock.now()
        try:
            await session.flush()
        except IntegrityError:
            raise TelescreenError(ErrorCode.CONFLICT, "alert rule already exists") from None
        await self._audit(session, actor_ref, "alert_rule.updated", row)
        return row

    async def delete(self, session: AsyncSession, rule_id: str, actor_ref: str) -> None:
        """Delete a rule and record an id/name-only audit event."""
        row: AlertRuleRow = await self.get(session, rule_id)
        await session.delete(row)
        await session.flush()
        await self._audit(session, actor_ref, "alert_rule.deleted", row)

    async def set_enabled(
        self, session: AsyncSession, rule_id: str, enabled: bool, actor_ref: str
    ) -> AlertRuleRow:
        """Enable or disable a rule."""
        row: AlertRuleRow = await self.get(session, rule_id)
        row.enabled = enabled
        row.updated_at = self.clock.now()
        await session.flush()
        await self._audit(
            session, actor_ref, "alert_rule.enabled" if enabled else "alert_rule.disabled", row
        )
        return row

    async def mute(
        self, session: AsyncSession, rule_id: str, minutes: int, actor_ref: str
    ) -> AlertRuleRow:
        """Mute a rule for a bounded duration."""
        if not 1 <= minutes <= 129600:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid mute duration")
        row: AlertRuleRow = await self.get(session, rule_id)
        row.muted_until = self.clock.now() + timedelta(minutes=minutes)
        row.updated_at = self.clock.now()
        await session.flush()
        await self._audit(session, actor_ref, "alert_rule.muted", row)
        return row

    async def unmute(self, session: AsyncSession, rule_id: str, actor_ref: str) -> AlertRuleRow:
        """Clear a rule's mute timestamp."""
        row: AlertRuleRow = await self.get(session, rule_id)
        row.muted_until = None
        row.updated_at = self.clock.now()
        await session.flush()
        await self._audit(session, actor_ref, "alert_rule.unmuted", row)
        return row

    async def history(
        self, session: AsyncSession, rule_id: str, cursor: str | None
    ) -> tuple[AlertFireRow, ...]:
        """Return a page of fire history using a timestamp/id keyset cursor."""
        await self.get(session, rule_id)
        before: datetime | None = None
        before_id: str | None = None
        if cursor is not None:
            timestamp: str = cursor
            if "|" in cursor:
                timestamp, before_id = cursor.rsplit("|", 1)
                if not before_id:
                    raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid cursor")
            try:
                before = datetime.fromisoformat(timestamp)
            except ValueError:
                raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid cursor") from None
        return await self.repository.history(session, rule_id, before, before_id)

    async def _audit(
        self, session: AsyncSession, actor_ref: str, action: str, row: AlertRuleRow
    ) -> None:
        """Append an audit event containing only stable identifiers and the safe rule name."""
        await self.audit.append(
            session,
            AuditEntry(
                ActorKind.MEMBER if actor_ref.startswith("member:") else ActorKind.SYSTEM,
                actor_ref,
                action,
                "alert_rule",
                row.id,
                action.replace("_", " "),
                json_object((("rule_id", json_str(row.id)), ("name", json_str(row.name)))),
            ),
        )
