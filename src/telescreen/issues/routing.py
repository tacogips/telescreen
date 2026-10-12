"""Automatic owner and team assignment for newly violating issues."""

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import json_int, json_object
from telescreen.core.vocabulary import ActorKind, IssueHistoryKind
from telescreen.db.models import IssueHistoryRow, IssueRow, OwnershipRuleRow, TeamRow
from telescreen.issues.ownership import match_rule
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.directory import AssigneeIds, OwnerDirectory, RecipientResolver
from telescreen.ports.policy import PolicyDecision

logger: logging.Logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IssueOwnerRouter:
    """Resolve and apply owner routing without exposing provider identities."""

    resolver: RecipientResolver
    owners: OwnerDirectory | None
    audit: AuditLog

    async def route(
        self,
        session: AsyncSession,
        issue: IssueRow,
        decision: PolicyDecision,
        now: datetime,
        default_team: str | None,
        owner_routing: bool,
    ) -> None:
        """Assign an unassigned issue using owner, policy-team, then default-team order."""
        if issue.assignee_member_id is not None or issue.assignee_team_id is not None:
            return
        recipients: list[str] = []
        if (decision.route_to_owner or owner_routing) and issue.owner_ref and self.owners:
            owner_member_ref: str | None = await self.owners.member_for_owner(
                session, issue.owner_ref
            )
            if owner_member_ref is not None:
                recipients.append(owner_member_ref)
        rule_rows: tuple[OwnershipRuleRow, ...] = tuple(
            (
                await session.scalars(
                    select(OwnershipRuleRow).order_by(
                        OwnershipRuleRow.position, OwnershipRuleRow.id
                    )
                )
            ).all()
        )
        for rule in rule_rows:
            if match_rule((rule,), issue.source, issue.display_locator) is None:
                continue
            rule_team: TeamRow | None = await session.get(TeamRow, rule.team_id)
            if rule_team is None:
                logger.warning("issue owner routing skipped: ownership_team_missing")
                continue
            recipients.append(f"team:{rule_team.slug}")
            break
        if decision.owner_team is not None:
            recipients.append(f"team:{decision.owner_team}")
        if default_team is not None:
            recipients.append(f"team:{default_team}")
        for recipient in recipients:
            try:
                assigned: AssigneeIds = await self.resolver.assignee_of(session, recipient)
            except TelescreenError as error:
                logger.warning("issue owner routing skipped: %s", error.code.value)
                continue
            issue.assignee_member_id = assigned.member_id
            issue.assignee_team_id = assigned.team_id
            issue.updated_at = now
            session.add(
                IssueHistoryRow(
                    id=new_id(),
                    issue_id=issue.id,
                    kind=IssueHistoryKind.ASSIGNED.value,
                    actor_ref="system:owner-routing",
                    from_value=None,
                    to_value=recipient,
                    note=None,
                    at=now,
                )
            )
            await self.audit.append(
                session,
                AuditEntry(
                    ActorKind.SYSTEM,
                    "system:owner-routing",
                    "issue.assigned",
                    "issue",
                    issue.id,
                    "Issue assigned",
                    json_object((("opened", json_int(0)), ("findings_inserted", json_int(0)))),
                ),
            )
            return
