"""Validated escalation policy CRUD service."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import dump_json, json_object, json_str
from telescreen.core.vocabulary import ActorKind, ErrorCode, SourceFormat
from telescreen.db.models import EscalationPolicyRow
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.workflow.dsl.escalation import (
    EscalationPolicySpec,
    escalation_to_json,
    load_escalation_policy,
)
from telescreen.workflow.dsl.raw_scan import source_has_raw_values


@dataclass
class EscalationPolicyService:
    """Create, replace, and list validated escalation policies."""

    audit: AuditLog
    clock: Clock
    redactor: Redactor

    async def create(
        self,
        session: AsyncSession,
        text: str,
        source_format: SourceFormat,
        actor_ref: str,
    ) -> EscalationPolicyRow:
        """Create a policy after validating its source document."""
        self._reject_sensitive_text(text)
        spec: EscalationPolicySpec = load_escalation_policy(text, source_format)
        row: EscalationPolicyRow = EscalationPolicyRow(
            id=new_id(),
            name=spec.name,
            tiers_json=dump_json(escalation_to_json(spec)),
            created_at=self.clock.now(),
            updated_at=self.clock.now(),
        )
        session.add(row)
        await session.flush()
        await self._audit(session, row, actor_ref, "escalation_policy.created", spec)
        return row

    async def update(
        self,
        session: AsyncSession,
        name: str,
        text: str,
        source_format: SourceFormat,
        actor_ref: str,
    ) -> EscalationPolicyRow:
        """Replace the tiers of an existing policy."""
        self._reject_sensitive_text(text)
        spec: EscalationPolicySpec = load_escalation_policy(text, source_format)
        row: EscalationPolicyRow | None = await session.scalar(
            select(EscalationPolicyRow).where(EscalationPolicyRow.name == name)
        )
        if row is None:
            from telescreen.core.errors import TelescreenError
            from telescreen.core.vocabulary import ErrorCode

            raise TelescreenError(ErrorCode.NOT_FOUND, "escalation policy not found")
        row.tiers_json = dump_json(escalation_to_json(spec))
        row.updated_at = self.clock.now()
        await self._audit(session, row, actor_ref, "escalation_policy.updated", spec)
        return row

    async def get(self, session: AsyncSession, name: str) -> EscalationPolicyRow | None:
        """Read one policy by name."""
        return await session.scalar(
            select(EscalationPolicyRow).where(EscalationPolicyRow.name == name)
        )

    def _reject_sensitive_text(self, text: str) -> None:
        """Reject sensitive source before it can be stored or audited."""
        if source_has_raw_values(self.redactor, text):
            raise TelescreenError(
                ErrorCode.RAW_VALUE_DETECTED, "Escalation policy contains sensitive data"
            )

    async def list(self, session: AsyncSession) -> Sequence[EscalationPolicyRow]:
        """Return all policies in name order."""
        return tuple(
            (
                await session.scalars(
                    select(EscalationPolicyRow).order_by(EscalationPolicyRow.name)
                )
            ).all()
        )

    async def _audit(
        self,
        session: AsyncSession,
        row: EscalationPolicyRow,
        actor_ref: str,
        action: str,
        spec: EscalationPolicySpec,
    ) -> None:
        """Append a metadata-only policy audit event."""
        await self.audit.append(
            session,
            AuditEntry(
                ActorKind.MEMBER if actor_ref.startswith("member:") else ActorKind.SYSTEM,
                actor_ref,
                action,
                "escalation_policy",
                row.id,
                "Escalation policy updated",
                json_object((("name", json_str(row.name)),)),
            ),
        )
