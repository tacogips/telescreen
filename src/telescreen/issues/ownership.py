"""Ownership rules, safe locator testing and assignee suggestions."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import json_object, json_str
from telescreen.core.vocabulary import ActorKind, ErrorCode, SuggestionReason
from telescreen.db.models import IssueRow, OwnershipRuleRow, PlacementPolicyRow, TeamRow
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.directory import OwnerDirectory
from telescreen.ports.issues import AssigneeSuggester, SuggestedAssignee


@dataclass(frozen=True)
class RuleMatch:
    """Safe ownership rule match result."""

    rule_id: str
    team_slug: str


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Compile the ownership glob grammar to a case-sensitive path regex."""
    parts: list[str] = ["^"]
    index: int = 0
    while index < len(pattern):
        char: str = pattern[index]
        if char == "*" and index + 1 < len(pattern) and pattern[index + 1] == "*":
            parts.append(".*")
            index += 2
        elif char == "*":
            parts.append("[^/]*")
            index += 1
        elif char == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(char))
            index += 1
    parts.append("$")
    return re.compile("".join(parts))


def match_rule(
    rules: Sequence[OwnershipRuleRow], source: str, locator: str
) -> OwnershipRuleRow | None:
    """Return the first enabled rule matching source and display locator."""
    ordered: Sequence[OwnershipRuleRow] = sorted(rules, key=lambda rule: (rule.position, rule.id))
    return next(
        (
            rule
            for rule in ordered
            if rule.enabled
            and (rule.source is None or rule.source == source)
            and _glob_regex(rule.pattern).fullmatch(locator)
        ),
        None,
    )


@dataclass
class OwnershipRuleService:
    """Manage ordered ownership rules and guard text with redaction."""

    redactor: Redactor
    audit: AuditLog
    clock: Clock

    async def list(self, session: AsyncSession) -> tuple[OwnershipRuleRow, ...]:
        """List ownership rules in configured order."""
        result: Sequence[OwnershipRuleRow] = tuple(
            (
                await session.scalars(
                    select(OwnershipRuleRow).order_by(
                        OwnershipRuleRow.position, OwnershipRuleRow.id
                    )
                )
            ).all()
        )
        return result

    def _validate(self, text: str) -> None:
        """Reject oversized or sensitive rule text without echoing it."""
        if len(text) > 512 or self.redactor.has_hits(text):
            raise TelescreenError(
                ErrorCode.VALIDATION_ERROR,
                "raw_value_detected"
                if self.redactor.has_hits(text)
                else "ownership text is too long",
            )

    async def _team_id(self, session: AsyncSession, slug: str) -> str:
        """Resolve an ownership team slug or raise a safe validation error."""
        team: TeamRow | None = await session.scalar(select(TeamRow).where(TeamRow.slug == slug))
        if team is None:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "unknown ownership team")
        return team.id

    async def create(
        self,
        session: AsyncSession,
        source: str | None,
        pattern: str,
        team_slug: str,
        enabled: bool,
        actor: str,
    ) -> OwnershipRuleRow:
        """Create an ownership rule and audit only its identifier and action."""
        self._validate(pattern)
        if source is not None:
            self._validate(source)
        team_id: str = await self._team_id(session, team_slug)
        rows: tuple[OwnershipRuleRow, ...] = await self.list(session)
        now: datetime = self.clock.now()
        row: OwnershipRuleRow = OwnershipRuleRow(
            id=new_id(),
            position=len(rows),
            source=source,
            pattern=pattern,
            team_id=team_id,
            enabled=enabled,
            created_by=actor,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await self._audit(session, row.id, "created", actor)
        return row

    async def update(
        self,
        session: AsyncSession,
        rule_id: str,
        source: str | None,
        pattern: str,
        team_slug: str,
        enabled: bool,
        actor: str,
    ) -> OwnershipRuleRow:
        """Update a rule after safe validation."""
        row: OwnershipRuleRow | None = await session.get(OwnershipRuleRow, rule_id)
        if row is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "ownership rule not found")
        self._validate(pattern)
        if source is not None:
            self._validate(source)
        row.source = source
        row.pattern = pattern
        row.team_id = await self._team_id(session, team_slug)
        row.enabled = enabled
        row.updated_at = self.clock.now()
        await self._audit(session, row.id, "updated", actor)
        return row

    async def delete(self, session: AsyncSession, rule_id: str, actor: str) -> None:
        """Delete a rule and audit its identifier."""
        row: OwnershipRuleRow | None = await session.get(OwnershipRuleRow, rule_id)
        if row is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "ownership rule not found")
        await session.delete(row)
        await self._audit(session, row.id, "deleted", actor)

    async def reorder(self, session: AsyncSession, ids: tuple[str, ...], actor: str) -> None:
        """Reorder all ownership rules exactly once, rejecting partial orders."""
        rows: tuple[OwnershipRuleRow, ...] = await self.list(session)
        if (
            len(ids) != len(rows)
            or len(set(ids)) != len(ids)
            or any(not any(row.id == rule_id for row in rows) for rule_id in ids)
        ):
            raise TelescreenError(
                ErrorCode.VALIDATION_ERROR, "ownership rule ids must include every rule once"
            )
        for position, rule_id in enumerate(ids):
            row: OwnershipRuleRow = next(row for row in rows if row.id == rule_id)
            row.position = position
            row.updated_at = self.clock.now()
        await self._audit(session, "ownership-rules", "reordered", actor)

    async def test(self, session: AsyncSession, source: str, locator: str) -> RuleMatch | None:
        """Return the matching rule and team slug without echoing input."""
        self._validate(locator)
        self._validate(source)
        row: OwnershipRuleRow | None = match_rule(await self.list(session), source, locator)
        if row is None:
            return None
        team: TeamRow | None = await session.get(TeamRow, row.team_id)
        return RuleMatch(row.id, team.slug) if team is not None else None

    async def _audit(self, session: AsyncSession, rule_id: str, action: str, actor: str) -> None:
        """Append an ownership audit record containing only id and action."""
        kind: ActorKind = ActorKind.MEMBER if actor.startswith("member:") else ActorKind.SYSTEM
        await self.audit.append(
            session,
            AuditEntry(
                kind,
                actor,
                "ownership_rule.changed",
                "ownership_rule",
                rule_id,
                "Ownership rule changed",
                json_object((("action", json_str(action)),)),
            ),
        )


@dataclass(frozen=True)
class OwnershipAssigneeSuggester(AssigneeSuggester):
    """Suggest owner, matching-rule and policy-team assignees in order."""

    owners: OwnerDirectory

    async def suggest(self, session: AsyncSession, issue_id: str) -> tuple[SuggestedAssignee, ...]:
        """Return ordered, deduplicated assignee candidates for an issue."""
        issue: IssueRow | None = await session.get(IssueRow, issue_id)
        if issue is None:
            return ()
        candidates: list[SuggestedAssignee] = []
        if issue.owner_ref is not None:
            member_ref: str | None = await self.owners.member_for_owner(session, issue.owner_ref)
            if member_ref is not None:
                candidates.append(SuggestedAssignee(member_ref, SuggestionReason.OWNER))
        rows: Sequence[OwnershipRuleRow] = tuple(
            (
                await session.scalars(
                    select(OwnershipRuleRow).order_by(
                        OwnershipRuleRow.position, OwnershipRuleRow.id
                    )
                )
            ).all()
        )
        rule: OwnershipRuleRow | None = match_rule(rows, issue.source, issue.display_locator)
        if rule is not None:
            team: TeamRow | None = await session.get(TeamRow, rule.team_id)
            if team is not None:
                candidates.append(
                    SuggestedAssignee(f"team:{team.slug}", SuggestionReason.OWNERSHIP_RULE)
                )
        policy: PlacementPolicyRow | None = None
        if issue.policy_name is not None:
            policy = await session.scalar(
                select(PlacementPolicyRow).where(
                    PlacementPolicyRow.name == issue.policy_name,
                    PlacementPolicyRow.active.is_(True),
                )
            )
        if policy is not None and policy.owner_team is not None:
            candidates.append(
                SuggestedAssignee(f"team:{policy.owner_team}", SuggestionReason.POLICY_TEAM)
            )
        unique: list[SuggestedAssignee] = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate.ref not in seen:
                seen.add(candidate.ref)
                unique.append(candidate)
        return tuple(unique)
