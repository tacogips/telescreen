"""Member role management with last-admin protection."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.json import json_object, json_str
from telescreen.core.permissions import parse_role
from telescreen.core.vocabulary import ErrorCode, IdentityKind, Role, RoleChangeSource
from telescreen.db.models import MemberRow
from telescreen.directory.actors import actor_kind_for
from telescreen.directory.repository import DirectoryRepository
from telescreen.ports.audit import AuditEntry, AuditLog


@dataclass(frozen=True)
class UserView:
    """Administrative view of one member and its access role."""

    handle: str
    display_name: str
    email: str | None
    role: Role | None
    idp_managed: bool
    active: bool
    last_sign_in_at: datetime | None
    teams: tuple[str, ...]


def stored_role(value: str | None) -> Role | None:
    """Parse a role column value, treating NULL as unassigned."""
    return parse_role(value) if value is not None else None


def _role_label(role: Role | None) -> str:
    """Return the audit label of a role."""
    return role.value if role is not None else "none"


@dataclass
class RoleService:
    """Assign member roles and list users with their roles and teams."""

    audit: AuditLog
    clock: Clock
    repository: DirectoryRepository = field(default_factory=DirectoryRepository)

    async def set_role(
        self,
        session: AsyncSession,
        handle: str,
        role: Role | None,
        actor_ref: str,
        source: RoleChangeSource,
    ) -> MemberRow:
        """Change a member role, refusing to remove the last active admin."""
        member: MemberRow | None = await self.repository.member_by_handle(session, handle)
        if member is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "member not found")
        current: Role | None = stored_role(member.role)
        if current is role:
            return member
        if current is Role.ADMIN and member.active and source is not RoleChangeSource.IDP:
            admins: Sequence[MemberRow] = await self.repository.active_admins_for_update(session)
            if not any(admin.id != member.id for admin in admins):
                raise TelescreenError(ErrorCode.LAST_ADMIN, "last admin cannot be removed")
        member.role = role.value if role is not None else None
        member.updated_at = self.clock.now()
        await self.audit.append(
            session,
            AuditEntry(
                actor_kind=actor_kind_for(actor_ref),
                actor_ref=actor_ref,
                action="role.changed",
                target_kind="member",
                target_id=member.id,
                summary="Member role changed",
                details=json_object(
                    (
                        ("from", json_str(_role_label(current))),
                        ("to", json_str(_role_label(role))),
                        ("source", json_str(source.value)),
                    )
                ),
            ),
        )
        return member

    async def list_users(
        self, session: AsyncSession, idp_role_mapping: bool
    ) -> tuple[UserView, ...]:
        """List members with role, IdP management flag and team slugs."""
        members: Sequence[MemberRow] = await self.repository.list_members(session)
        oidc_members: set[str] = await self.repository.member_ids_with_identity_kind(
            session, IdentityKind.OIDC_SUBJECT.value
        )
        pairs: Sequence[Row[tuple[str, str]]] = await self.repository.team_slugs_by_member(session)
        return tuple(
            UserView(
                handle=member.handle,
                display_name=member.display_name,
                email=member.email,
                role=stored_role(member.role),
                idp_managed=idp_role_mapping and member.id in oidc_members,
                active=member.active,
                last_sign_in_at=member.last_sign_in_at,
                teams=tuple(slug for member_id, slug in pairs if member_id == member.id),
            )
            for member in members
        )
