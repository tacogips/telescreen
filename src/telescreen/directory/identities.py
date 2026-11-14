"""Keyed member identity management."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.ids import new_id
from telescreen.core.json import json_object, json_str
from telescreen.core.vocabulary import ActorKind, ErrorCode, IdentityKind, IdentityOrigin
from telescreen.db.models import MemberIdentityRow, MemberRow
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.ports.audit import AuditEntry, AuditLog
from telescreen.ports.collectors import OwnerIdentity


@dataclass(frozen=True)
class IdentityView:
    """Safe member identity fields for administrative display."""

    id: str
    kind: str
    origin: str
    key_prefix: str
    created_at: datetime


@dataclass
class IdentityService:
    """Derive, add, list and remove keyed member identities."""

    fingerprinter: Fingerprinter
    audit: AuditLog
    clock: Clock

    @staticmethod
    def _key_parts(fingerprinter: Fingerprinter, kind: IdentityKind, value: str) -> tuple[str, str]:
        """Return the normalized identity kind and keyed digest."""
        owner_ref: str = fingerprinter.owner_ref(OwnerIdentity(kind, value))
        parts: tuple[str, str, str] = owner_ref.partition(":")
        separator: str = parts[1]
        key: str = parts[2]
        if not separator or len(key) != 64:
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "identity reference is invalid")
        return kind.value, key

    async def derive_for_member(self, session: AsyncSession, member: MemberRow) -> None:
        """Synchronize derived email and Slack identity rows for one member."""
        desired: list[tuple[IdentityKind, IdentityOrigin, str]] = []
        if member.email:
            desired.append((IdentityKind.EMAIL, IdentityOrigin.MEMBER_EMAIL, member.email))
        if member.slack_user_id:
            desired.append(
                (IdentityKind.SLACK_USER, IdentityOrigin.MEMBER_SLACK, member.slack_user_id)
            )
        rows: list[MemberIdentityRow] = list(
            (
                await session.scalars(
                    select(MemberIdentityRow).where(
                        MemberIdentityRow.member_id == member.id,
                        MemberIdentityRow.origin.in_(
                            (IdentityOrigin.MEMBER_EMAIL.value, IdentityOrigin.MEMBER_SLACK.value)
                        ),
                    )
                )
            ).all()
        )
        desired_keys: list[tuple[str, str]] = []
        for kind, origin, value in desired:
            kind_value: str
            key: str
            kind_value, key = self._key_parts(self.fingerprinter, kind, value)
            desired_keys.append((kind_value, key))
            existing: MemberIdentityRow | None = await session.scalar(
                select(MemberIdentityRow).where(
                    MemberIdentityRow.kind == kind_value,
                    MemberIdentityRow.identity_key == key,
                )
            )
            if existing is not None and existing.member_id != member.id:
                raise TelescreenError(ErrorCode.CONFLICT, "identity is already assigned")
            if existing is None:
                session.add(
                    MemberIdentityRow(
                        id=new_id(),
                        member_id=member.id,
                        kind=kind_value,
                        identity_key=key,
                        origin=origin.value,
                        created_at=self.clock.now(),
                    )
                )
            else:
                existing.origin = origin.value
        for row in rows:
            if (row.kind, row.identity_key) not in desired_keys:
                await session.delete(row)

    async def add(
        self,
        session: AsyncSession,
        handle: str,
        kind: IdentityKind,
        value: str,
        actor_ref: str,
    ) -> MemberIdentityRow:
        """Add a manually configured identity without retaining its value."""
        member: MemberRow | None = await session.scalar(
            select(MemberRow).where(MemberRow.handle == handle)
        )
        if member is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "member not found")
        if not value.strip():
            raise TelescreenError(ErrorCode.VALIDATION_ERROR, "identity value is required")
        if kind is IdentityKind.OIDC_SUBJECT:
            raise TelescreenError(
                ErrorCode.VALIDATION_ERROR, "oidc identities are linked only by sign-in"
            )
        kind_value: str
        key: str
        kind_value, key = self._key_parts(self.fingerprinter, kind, value)
        existing: MemberIdentityRow | None = await session.scalar(
            select(MemberIdentityRow).where(
                MemberIdentityRow.kind == kind_value,
                MemberIdentityRow.identity_key == key,
            )
        )
        if existing is not None:
            raise TelescreenError(ErrorCode.CONFLICT, "identity is already assigned")
        row: MemberIdentityRow = MemberIdentityRow(
            id=new_id(),
            member_id=member.id,
            kind=kind_value,
            identity_key=key,
            origin=IdentityOrigin.MANUAL.value,
            created_at=self.clock.now(),
        )
        session.add(row)
        await self.audit.append(
            session,
            AuditEntry(
                actor_kind=ActorKind.MEMBER,
                actor_ref=actor_ref,
                action="identity.added",
                target_kind="member_identity",
                target_id=row.id,
                summary="Member identity added",
                details=json_object((("kind", json_str(kind_value)),)),
            ),
        )
        return row

    async def list(self, session: AsyncSession, handle: str) -> tuple[IdentityView, ...]:
        """List identity metadata without revealing identity values or full keys."""
        member: MemberRow | None = await session.scalar(
            select(MemberRow).where(MemberRow.handle == handle)
        )
        if member is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "member not found")
        rows: list[MemberIdentityRow] = list(
            (
                await session.scalars(
                    select(MemberIdentityRow)
                    .where(MemberIdentityRow.member_id == member.id)
                    .order_by(MemberIdentityRow.created_at, MemberIdentityRow.id)
                )
            ).all()
        )
        return tuple(
            IdentityView(row.id, row.kind, row.origin, row.identity_key[:8], row.created_at)
            for row in rows
        )

    async def remove(
        self, session: AsyncSession, handle: str, identity_id: str, actor_ref: str
    ) -> None:
        """Remove a manually configured identity and audit the operation."""
        member: MemberRow | None = await session.scalar(
            select(MemberRow).where(MemberRow.handle == handle)
        )
        if member is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "member not found")
        row: MemberIdentityRow | None = await session.scalar(
            select(MemberIdentityRow).where(
                MemberIdentityRow.id == identity_id,
                MemberIdentityRow.member_id == member.id,
            )
        )
        if row is None:
            raise TelescreenError(ErrorCode.NOT_FOUND, "member identity not found")
        if row.origin != IdentityOrigin.MANUAL.value:
            raise TelescreenError(ErrorCode.CONFLICT, "derived identities cannot be removed")
        await session.delete(row)
        await self.audit.append(
            session,
            AuditEntry(
                actor_kind=ActorKind.MEMBER,
                actor_ref=actor_ref,
                action="identity.removed",
                target_kind="member_identity",
                target_id=row.id,
                summary="Member identity removed",
                details=json_object((("kind", json_str(row.kind)),)),
            ),
        )
