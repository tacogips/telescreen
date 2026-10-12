"""Member profile administration and deactivation handling."""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.engine import ScalarResult
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.json import json_array, json_object, json_str
from telescreen.core.permissions import parse_role
from telescreen.core.vocabulary import UNRESOLVED_STATUSES, ErrorCode, IssueStatus, Role
from telescreen.db.models import IssueRow, MemberRow
from telescreen.directory.actors import actor_kind_for
from telescreen.directory.repository import DirectoryRepository
from telescreen.directory.roles import RoleService
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.issues import IssueRemediation

_EMAIL: re.Pattern[str] = re.compile(r"^[^\s,;<>@]+@[^\s,;<>@]+$")
_SLACK_ID: re.Pattern[str] = re.compile(r"^[UW][A-Z0-9]{8,}$")


@dataclass
class MemberAdminService:
    """Update member profile fields and safely deactivate member accounts."""

    repository: DirectoryRepository
    roles: RoleService
    remediation: IssueRemediation | None
    audit: AuditLog
    clock: Clock
    redactor: Redactor = field(default_factory=Redactor.default)

    async def update(
        self,
        session: AsyncSession,
        handle: str,
        display_name: str | None,
        email: str | None,
        slack_user_id: str | None,
        actor: str,
    ) -> MemberRow:
        """Update supplied fields after validating member directory values."""
        member: MemberRow | None = await self.repository.member_by_handle(session, handle)
        if member is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "member not found")
        fields: list[str] = []
        if display_name is not None:
            if self.redactor.has_hits(display_name):
                raise TelescreenError(ErrorCode.VALIDATION_ERROR, "raw_value_detected")
            member.display_name = display_name
            fields.append("display_name")
        if email is not None:
            if len(email) > 254 or not _EMAIL.fullmatch(email) or email.count("@") != 1:
                raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid member email")
            member.email = email
            fields.append("email")
        if slack_user_id is not None:
            if self.redactor.has_hits(slack_user_id):
                raise TelescreenError(ErrorCode.VALIDATION_ERROR, "raw_value_detected")
            if not _SLACK_ID.fullmatch(slack_user_id):
                raise TelescreenError(ErrorCode.VALIDATION_ERROR, "invalid Slack user id")
            member.slack_user_id = slack_user_id
            fields.append("slack_user_id")
        if fields:
            member.updated_at = self.clock.now()
            await self.audit.append(
                session,
                AuditEntry(
                    actor_kind_for(actor),
                    actor,
                    "member.updated",
                    "member",
                    member.id,
                    "Member updated",
                    json_object(
                        (("fields", json_array(tuple(json_str(name) for name in fields))),)
                    ),
                ),
            )
        return member

    async def deactivate(self, session: AsyncSession, handle: str, actor: str) -> MemberRow:
        """Deactivate a member, protecting the last admin and releasing open issues."""
        member: MemberRow | None = await self.repository.member_by_handle(session, handle)
        if member is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "member not found")
        if not member.active:
            return member
        if member.role is not None and parse_role(member.role) is Role.ADMIN:
            admins: Sequence[MemberRow] = await self.repository.active_admins_for_update(session)
            if not any(row.id != member.id for row in admins):
                raise TelescreenError(ErrorCode.LAST_ADMIN, "last admin cannot be deactivated")
        issues_result: ScalarResult[IssueRow] = await session.scalars(
            select(IssueRow).where(
                IssueRow.assignee_member_id == member.id,
                IssueRow.status.in_(
                    tuple(status.value for status in UNRESOLVED_STATUSES)
                    + (IssueStatus.ARCHIVED.value,)
                ),
            )
        )
        issues: Sequence[IssueRow] = issues_result.all()
        if issues and self.remediation is None:
            raise TelescreenError(ErrorCode.INTERNAL_ERROR, "issue remediation unavailable")
        for issue in issues:
            if self.remediation is not None:
                await self.remediation.release_to_team(session, issue.id, "system:deactivation")
        member.active = False
        member.updated_at = self.clock.now()
        await self.audit.append(
            session,
            AuditEntry(
                actor_kind_for(actor),
                actor,
                "member.deactivated",
                "member",
                member.id,
                "Member deactivated",
                json_object(()),
            ),
        )
        return member

    async def reactivate(self, session: AsyncSession, handle: str, actor: str) -> MemberRow:
        """Reactivate a previously deactivated member and record the change."""
        member: MemberRow | None = await self.repository.member_by_handle(session, handle)
        if member is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "member not found")
        if member.active:
            return member
        member.active = True
        member.updated_at = self.clock.now()
        await self.audit.append(
            session,
            AuditEntry(
                actor_kind_for(actor),
                actor,
                "member.reactivated",
                "member",
                member.id,
                "Member reactivated",
                json_object(()),
            ),
        )
        return member
