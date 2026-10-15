"""Pure async readers for outbound directory and issue-link projections."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.db.models import (
    ApiTokenRow,
    CollectorConfigRow,
    IssueRow,
    MemberIdentityRow,
    MemberRow,
    OwnershipRuleRow,
    TeamMembershipRow,
    TeamRow,
)
from telescreen.issues.keys import short_id
from telescreen.ports.directory_projection import (
    ApiTokenEntity,
    CollectorEntity,
    DirectoryEntity,
    DirectoryEntityKind,
    DirectoryPayload,
    IdentityEntity,
    MemberEntity,
    OwnershipRuleEntry,
    OwnershipRulesEntity,
    TeamEntity,
)
from telescreen.ports.inventory_commands import IssueLinkState


async def read_directory_entity(
    session: AsyncSession, kind: DirectoryEntityKind, entity_id: str, revision: int
) -> DirectoryEntity:
    """Read one current directory state, returning a tombstone when it was removed."""
    payload: DirectoryPayload | None
    if kind is DirectoryEntityKind.MEMBER:
        row: MemberRow | None = await session.get(MemberRow, entity_id)
        payload = (
            MemberEntity(
                row.id,
                row.handle,
                row.display_name,
                row.active,
                row.role,
                row.email,
                row.slack_user_id,
            )
            if row is not None
            else None
        )
    elif kind is DirectoryEntityKind.TEAM:
        row = await session.get(TeamRow, entity_id)
        memberships: tuple[str, ...] = (
            tuple(
                await session.scalars(
                    select(TeamMembershipRow.member_id)
                    .where(TeamMembershipRow.team_id == entity_id)
                    .order_by(TeamMembershipRow.member_id)
                )
            )
            if row is not None
            else ()
        )
        payload = (
            TeamEntity(
                row.id,
                row.slug,
                row.name,
                row.lead_member_id,
                memberships,
                row.email,
                row.slack_channel,
            )
            if row is not None
            else None
        )
    elif kind is DirectoryEntityKind.IDENTITY:
        row = await session.get(MemberIdentityRow, entity_id)
        payload = (
            IdentityEntity(row.id, row.member_id, row.kind, row.identity_key)
            if row is not None
            else None
        )
    elif kind is DirectoryEntityKind.API_TOKEN:
        row = await session.get(ApiTokenRow, entity_id)
        payload = (
            ApiTokenEntity(
                row.id,
                row.label,
                row.token_hash,
                tuple(scope for scope in row.scopes.split(",") if scope),
                row.member_id,
                row.revoked_at is not None,
            )
            if row is not None
            else None
        )
    elif kind is DirectoryEntityKind.OWNERSHIP_RULES:
        rows: tuple[OwnershipRuleRow, ...] = tuple(
            (
                await session.scalars(
                    select(OwnershipRuleRow).order_by(
                        OwnershipRuleRow.position, OwnershipRuleRow.id
                    )
                )
            ).all()
        )
        payload = OwnershipRulesEntity(
            tuple(
                OwnershipRuleEntry(
                    row.id, row.position, row.source, row.pattern, row.team_id, row.enabled
                )
                for row in rows
            )
        )
        entity_id = "rules"
    else:
        row = await session.get(CollectorConfigRow, entity_id)
        payload = CollectorEntity(row.id, row.name) if row is not None else None
    return DirectoryEntity(kind, entity_id, revision, payload is not None, payload)


async def read_issue_link(session: AsyncSession, issue_id: str) -> IssueLinkState:
    """Read only issue-to-resource enrichment fields for Inventory."""
    row: IssueRow | None = await session.get(IssueRow, issue_id)
    if row is None:
        return IssueLinkState(issue_id, "", "", "", None, False)
    return IssueLinkState(
        row.id, row.resource_key, short_id(row.number), row.status, row.owner_ref, True
    )
