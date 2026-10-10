"""Versioned placement policy storage and re-evaluation scheduling."""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock, ensure_utc
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import dump_json, json_int, json_object, json_str
from telescreen.core.vocabulary import (
    ActorKind,
    EngineTaskKind,
    ErrorCode,
    PolicyEffect,
    SourceFormat,
)
from telescreen.db.models import PlacementPolicyRow
from telescreen.detection import RuleDetector
from telescreen.issues.policy.loader import (
    PolicyValidationError,
    load_policy,
    policy_to_json,
)
from telescreen.issues.policy.model import PolicyDefinition, PolicyVersionView
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.directory import RecipientResolver
from telescreen.ports.workflow import TaskQueue, TaskSpec
from telescreen.workflow.dsl.validate import ValidationIssue


@dataclass
class PolicyStore:
    """Manage immutable policy versions inside caller-owned transactions."""

    redactor: Redactor
    detector: RuleDetector
    audit: AuditLog
    queue: TaskQueue
    resolver: RecipientResolver
    clock: Clock

    def validate(self, text: str, fmt: SourceFormat) -> tuple[ValidationIssue, ...]:
        """Return path/code-only static diagnostics without retaining source text."""
        if self.detector.scan_text(text):
            return (ValidationIssue("", "raw_value_detected"),)
        try:
            load_policy(text, fmt)
        except PolicyValidationError as error:
            return error.issues
        return ()

    async def create(
        self, session: AsyncSession, text: str, fmt: SourceFormat, actor_ref: str
    ) -> PolicyVersionView:
        """Create the next inactive version after privacy and owner checks."""
        if self.detector.scan_text(text):
            raise PolicyValidationError(
                (self._issue("", "raw_value_detected"),), ErrorCode.RAW_VALUE_DETECTED
            )
        policy: PolicyDefinition = load_policy(text, fmt)
        if policy.owner_team is not None:
            try:
                await self.resolver.assignee_of(session, f"team:{policy.owner_team}")
            except TelescreenError as error:
                if error.code is not ErrorCode.RECIPIENT_UNKNOWN:
                    raise
                raise PolicyValidationError((self._issue("/owner_team", "unknown_team"),)) from None
        version_value: int | None = await session.scalar(
            select(func.max(PlacementPolicyRow.version)).where(
                PlacementPolicyRow.name == policy.name
            )
        )
        version: int = (version_value or 0) + 1
        redacted_policy: PolicyDefinition = PolicyDefinition(
            policy.name,
            policy.effect,
            policy.rule_classes,
            policy.sources,
            policy.locations,
            policy.max_exposure,
            policy.max_age_days,
            policy.route_to_owner,
            policy.owner_team,
            self.redactor.redact(policy.rationale).text,
        )
        row: PlacementPolicyRow = PlacementPolicyRow(
            id=new_id(),
            name=policy.name,
            version=version,
            source_format=fmt.value,
            source_text=self.redactor.redact(text).text,
            compiled_json=dump_json(policy_to_json(redacted_policy)),
            effect=policy.effect.value,
            enabled=True,
            active=False,
            owner_team=policy.owner_team,
            created_by=actor_ref,
            created_at=self.clock.now(),
        )
        session.add(row)
        await session.flush()
        await self.audit.append(
            session,
            AuditEntry(
                ActorKind.MEMBER if actor_ref.startswith("member:") else ActorKind.SYSTEM,
                actor_ref,
                "policy.created",
                "placement_policy",
                row.id,
                "Placement policy version created",
                json_object((("name", json_str(row.name)), ("version", json_int(row.version)))),
            ),
        )
        return self._view(row)

    async def activate(
        self, session: AsyncSession, name: str, version: int, actor_ref: str
    ) -> PolicyVersionView:
        """Activate one version, audit it, then enqueue policy re-evaluation."""
        target: PlacementPolicyRow | None = await session.scalar(
            select(PlacementPolicyRow).where(
                PlacementPolicyRow.name == name, PlacementPolicyRow.version == version
            )
        )
        if target is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "policy version not found")
        rows: Sequence[PlacementPolicyRow] = tuple(
            (
                await session.scalars(
                    select(PlacementPolicyRow).where(PlacementPolicyRow.name == name)
                )
            ).all()
        )
        for row in rows:
            row.active = row.id == target.id
            row.enabled = True
        await session.flush()
        sequence: int = await self.audit.append(
            session, self._audit_entry(actor_ref, "policy.activated", target, version)
        )
        await self._enqueue(session, target.id, name, version, "activated", sequence)
        return self._view(target)

    async def disable(self, session: AsyncSession, name: str, actor_ref: str) -> None:
        """Disable a policy name and enqueue a re-evaluation task."""
        rows: Sequence[PlacementPolicyRow] = tuple(
            (
                await session.scalars(
                    select(PlacementPolicyRow).where(PlacementPolicyRow.name == name)
                )
            ).all()
        )
        if not rows:
            raise TelescreenError(ErrorCode.NOT_FOUND, "policy not found")
        active: PlacementPolicyRow | None = next((row for row in rows if row.active), None)
        version: int = active.version if active is not None else 0
        for row in rows:
            row.active = False
            row.enabled = False
        await session.flush()
        target: PlacementPolicyRow = (
            active if active is not None else max(rows, key=lambda row: row.version)
        )
        sequence: int = await self.audit.append(
            session, self._audit_entry(actor_ref, "policy.disabled", target, version)
        )
        await self._enqueue(session, target.id, name, version, "disabled", sequence)

    async def list(self, session: AsyncSession) -> tuple[PolicyVersionView, ...]:
        """Return the active version or latest version once per policy name."""
        rows: Sequence[PlacementPolicyRow] = tuple(
            (
                await session.scalars(
                    select(PlacementPolicyRow).order_by(
                        PlacementPolicyRow.name, PlacementPolicyRow.version
                    )
                )
            ).all()
        )
        selected: list[PlacementPolicyRow] = []
        for row in rows:
            existing: int | None = next(
                (index for index, candidate in enumerate(selected) if candidate.name == row.name),
                None,
            )
            if existing is None:
                selected.append(row)
            elif row.active or (
                not selected[existing].active and row.version > selected[existing].version
            ):
                selected[existing] = row
        return tuple(self._view(row) for row in selected)

    async def versions(self, session: AsyncSession, name: str) -> tuple[PolicyVersionView, ...]:
        """Return every stored version in ascending order."""
        rows: Sequence[PlacementPolicyRow] = tuple(
            (
                await session.scalars(
                    select(PlacementPolicyRow)
                    .where(PlacementPolicyRow.name == name)
                    .order_by(PlacementPolicyRow.version)
                )
            ).all()
        )
        return tuple(self._view(row) for row in rows)

    async def version(self, session: AsyncSession, name: str, version: int) -> PolicyVersionView:
        """Return one named version or raise a stable not-found error."""
        row: PlacementPolicyRow | None = await session.scalar(
            select(PlacementPolicyRow).where(
                PlacementPolicyRow.name == name, PlacementPolicyRow.version == version
            )
        )
        if row is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "policy version not found")
        return self._view(row)

    async def _enqueue(
        self,
        session: AsyncSession,
        policy_id: str,
        name: str,
        version: int,
        action: str,
        audit_seq: int,
    ) -> None:
        await self.queue.enqueue(
            session,
            TaskSpec(
                EngineTaskKind.POLICY_REEVALUATE,
                policy_id,
                json_object((("name", json_str(name)),)),
                ensure_utc(self.clock.now()),
                f"policy:{name}:{version}:{action}:{audit_seq}",
            ),
        )

    @staticmethod
    def _issue(path: str, code: str) -> ValidationIssue:
        return ValidationIssue(path, code)

    @staticmethod
    def _view(row: PlacementPolicyRow) -> PolicyVersionView:
        return PolicyVersionView(
            row.name,
            row.version,
            PolicyEffect(row.effect),
            row.enabled,
            row.active,
            row.owner_team,
            SourceFormat(row.source_format),
            row.source_text,
            row.created_by,
            ensure_utc(row.created_at),
        )

    @staticmethod
    def _audit_entry(
        actor_ref: str, action: str, row: PlacementPolicyRow, version: int
    ) -> AuditEntry:
        return AuditEntry(
            ActorKind.MEMBER if actor_ref.startswith("member:") else ActorKind.SYSTEM,
            actor_ref,
            action,
            "placement_policy",
            row.id,
            "Placement policy state changed",
            json_object((("name", json_str(row.name)), ("version", json_int(version)))),
        )
