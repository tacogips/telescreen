"""v1.2 directory role management, recipient and token behavior tests."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.audit.service import AuditLogService
from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.json import JsonValue, dump_json, parse_json
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import (
    ApiScope,
    ErrorCode,
    IdentityKind,
    IdentityOrigin,
    MembershipOrigin,
    Role,
    RoleChangeSource,
)
from telescreen.db.models import (
    AuditRecordRow,
    MemberIdentityRow,
    MemberRow,
    TeamMembershipRow,
    TeamRow,
)
from telescreen.directory import (
    CreatedToken,
    DirectoryRecipientResolver,
    DirectoryService,
    RoleService,
    TokenService,
    TokenView,
    UserView,
)
from telescreen.directory.identities import IdentityService
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.directory import ResolvedRecipients
from tests.support.db import SqliteHarness, make_sqlite_harness

NOW: datetime = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def _member(member_id: str, handle: str, role: Role | None, active: bool = True) -> MemberRow:
    """Build a member row with an optional role."""
    return MemberRow(
        id=member_id,
        handle=handle,
        display_name=handle.title(),
        email=f"{handle}@example.test",
        slack_user_id=None,
        active=active,
        created_at=NOW,
        updated_at=NOW,
        role=role.value if role is not None else None,
    )


async def _audit_rows(session: AsyncSession, action: str) -> list[AuditRecordRow]:
    """Return the persisted audit records for one action."""
    return list(
        (
            await session.scalars(
                select(AuditRecordRow)
                .where(AuditRecordRow.action == action)
                .order_by(AuditRecordRow.seq)
            )
        ).all()
    )


def _details(row: AuditRecordRow) -> str:
    """Return a canonical JSON dump of audit details."""
    value: JsonValue = parse_json(row.details_json)
    return dump_json(value)


@pytest.mark.asyncio
async def test_set_role_audits_and_protects_the_last_admin() -> None:
    """Role changes are audited; the last active admin cannot be removed except by the IdP."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(NOW)
    audit: AuditLogService = AuditLogService(clock, Redactor.default())
    roles: RoleService = RoleService(audit, clock)
    async with harness.sessions.begin() as session:
        session.add(_member("m-admin", "root", Role.ADMIN))
        session.add(_member("m-viewer", "vera", Role.VIEWER))
        session.add(_member("m-off", "olga", Role.ADMIN, active=False))
        promoted: MemberRow = await roles.set_role(
            session, "vera", Role.ADMIN, "member:m-admin", RoleChangeSource.MANUAL
        )
        assert promoted.role == Role.ADMIN.value
        changed: list[AuditRecordRow] = await _audit_rows(session, "role.changed")
        assert len(changed) == 1
        assert changed[0].actor_kind == "member"
        assert changed[0].actor_ref == "member:m-admin"
        assert changed[0].target_id == "m-viewer"
        assert '"from":"viewer"' in _details(changed[0])
        assert '"to":"admin"' in _details(changed[0])
        assert '"source":"manual"' in _details(changed[0])
        unchanged: MemberRow = await roles.set_role(
            session, "vera", Role.ADMIN, "member:m-admin", RoleChangeSource.MANUAL
        )
        assert unchanged.role == Role.ADMIN.value
        assert len(await _audit_rows(session, "role.changed")) == 1
        demoted: MemberRow = await roles.set_role(
            session, "root", Role.RESPONDER, "system:cli", RoleChangeSource.CLI
        )
        assert demoted.role == Role.RESPONDER.value
        with pytest.raises(TelescreenError) as last_admin:
            await roles.set_role(
                session, "vera", Role.RESPONDER, "system:cli", RoleChangeSource.CLI
            )
        assert last_admin.value.code is ErrorCode.LAST_ADMIN
        assert last_admin.value.detail == "last admin cannot be removed"
        with pytest.raises(TelescreenError) as last_admin_none:
            await roles.set_role(session, "vera", None, "member:m-viewer", RoleChangeSource.MANUAL)
        assert last_admin_none.value.code is ErrorCode.LAST_ADMIN
        idp_demoted: MemberRow = await roles.set_role(
            session, "vera", Role.VIEWER, "system:auth", RoleChangeSource.IDP
        )
        assert idp_demoted.role == Role.VIEWER.value
        inactive: MemberRow = await roles.set_role(
            session, "olga", None, "system:cli", RoleChangeSource.CLI
        )
        assert inactive.role is None
        with pytest.raises(TelescreenError) as missing:
            await roles.set_role(session, "nobody", Role.VIEWER, "system:cli", RoleChangeSource.CLI)
        assert missing.value.code is ErrorCode.NOT_FOUND
        records: list[AuditRecordRow] = await _audit_rows(session, "role.changed")
        assert len(records) == 4
        assert records[2].actor_kind == "system"
        assert '"source":"idp"' in _details(records[2])
        assert '"to":"none"' in _details(records[3])
    await harness.close()


@pytest.mark.asyncio
async def test_list_users_reports_roles_teams_and_idp_management() -> None:
    """Users list roles, team slugs and idp_managed only while role mapping is on."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(NOW)
    audit: AuditLogService = AuditLogService(clock, Redactor.default())
    roles: RoleService = RoleService(audit, clock)
    async with harness.sessions.begin() as session:
        session.add(_member("m-a", "alice", Role.ADMIN))
        session.add(_member("m-b", "bob", None))
        session.add(TeamRow(id="t-1", slug="secops", name="SecOps", created_at=NOW, updated_at=NOW))
        session.add(TeamMembershipRow(team_id="t-1", member_id="m-a", origin="idp"))
        session.add(
            MemberIdentityRow(
                id="i-1",
                member_id="m-a",
                kind=IdentityKind.OIDC_SUBJECT.value,
                identity_key="a" * 64,
                origin=IdentityOrigin.OIDC.value,
                created_at=NOW,
            )
        )
        mapped: tuple[UserView, ...] = await roles.list_users(session, True)
        assert [view.handle for view in mapped] == ["alice", "bob"]
        assert mapped[0].role is Role.ADMIN
        assert mapped[0].idp_managed is True
        assert mapped[0].teams == ("secops",)
        assert mapped[1].role is None
        assert mapped[1].idp_managed is False
        assert mapped[1].teams == ()
        unmapped: tuple[UserView, ...] = await roles.list_users(session, False)
        assert unmapped[0].idp_managed is False
    await harness.close()


@pytest.mark.asyncio
async def test_set_team_members_keeps_v1_manual_semantics() -> None:
    """Replacing team members writes manual-origin rows only."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(NOW)
    audit: AuditLogService = AuditLogService(clock, Redactor.default())
    directory: DirectoryService = DirectoryService(audit, clock)
    async with harness.sessions.begin() as session:
        await directory.create_member(session, "alice", "Alice", None, None)
        await directory.create_team(session, "secops", "SecOps", None, None)
        await directory.set_team_members(session, "secops", ("alice",))
        rows: list[TeamMembershipRow] = list(
            (await session.scalars(select(TeamMembershipRow))).all()
        )
        assert [row.origin for row in rows] == [MembershipOrigin.MANUAL.value]
    await harness.close()


@pytest.mark.asyncio
async def test_slack_channel_recipient_resolves_to_one_channel() -> None:
    """slack_channel: references resolve to one Slack channel and reject user or DM ids."""
    harness: SqliteHarness = await make_sqlite_harness()
    resolver: DirectoryRecipientResolver = DirectoryRecipientResolver()
    async with harness.sessions.begin() as session:
        resolved: ResolvedRecipients = await resolver.resolve(session, "slack_channel:C0123ABCDE")
        assert resolved.slack_channels == ("C0123ABCDE",)
        assert resolved.emails == ()
        assert resolved.slack_user_ids == ()
        assert resolved.member_ids == ()
        legacy: ResolvedRecipients = await resolver.resolve(session, "slack:C0123ABCDE")
        assert legacy == resolved
        with pytest.raises(TelescreenError) as empty:
            await resolver.resolve(session, "slack_channel:")
        assert empty.value.code is ErrorCode.RECIPIENT_UNKNOWN
        for personal in ("slack_channel:U0123ABCDE", "slack_channel:D0123ABCDE"):
            with pytest.raises(TelescreenError) as not_channel:
                await resolver.resolve(session, personal)
            assert not_channel.value.code is ErrorCode.RECIPIENT_UNKNOWN
    await harness.close()


@pytest.mark.asyncio
async def test_identity_service_rejects_manual_oidc_identities() -> None:
    """Manual identity management cannot add or remove OIDC subject identities."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(NOW)
    audit: AuditLogService = AuditLogService(clock, Redactor.default())
    fingerprinter: Fingerprinter = Fingerprinter.from_secret(
        SecretValue("0123456789abcdef0123456789abcdef")
    )
    identities: IdentityService = IdentityService(fingerprinter, audit, clock)
    async with harness.sessions.begin() as session:
        session.add(_member("m-a", "alice", Role.ADMIN))
        session.add(
            MemberIdentityRow(
                id="i-oidc",
                member_id="m-a",
                kind=IdentityKind.OIDC_SUBJECT.value,
                identity_key="b" * 64,
                origin=IdentityOrigin.OIDC.value,
                created_at=NOW,
            )
        )
        with pytest.raises(TelescreenError) as added:
            await identities.add(
                session, "alice", IdentityKind.OIDC_SUBJECT, "issuer\0subject", "member:m-a"
            )
        assert added.value.code is ErrorCode.VALIDATION_ERROR
        with pytest.raises(TelescreenError) as removed:
            await identities.remove(session, "alice", "i-oidc", "member:m-a")
        assert removed.value.code is ErrorCode.CONFLICT
    await harness.close()


@pytest.mark.asyncio
async def test_tokens_list_bind_and_audit_the_calling_actor() -> None:
    """Tokens list member handles; audits carry the caller; unknown handles are NOT_FOUND."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(NOW)
    audit: AuditLogService = AuditLogService(clock, Redactor.default())
    tokens: TokenService = TokenService(audit, clock)
    async with harness.sessions.begin() as session:
        session.add(_member("m-a", "alice", Role.ADMIN))
        member_id: str = await tokens.member_id_for_handle(session, "alice")
        assert member_id == "m-a"
        with pytest.raises(TelescreenError) as missing:
            await tokens.member_id_for_handle(session, "nobody")
        assert missing.value.code is ErrorCode.NOT_FOUND
        bound: CreatedToken = await tokens.create(
            session,
            "ci",
            frozenset((ApiScope.READ, ApiScope.WRITE)),
            member_id,
            actor_ref="member:m-a",
        )
        clock.advance(timedelta(seconds=1))
        machine: CreatedToken = await tokens.create(
            session, "police", frozenset((ApiScope.INGEST,)), None, actor_ref="token:t-root"
        )
        clock.advance(timedelta(seconds=1))
        legacy: CreatedToken = await tokens.create(
            session, "legacy", frozenset((ApiScope.READ,)), None
        )
        await tokens.revoke(session, machine.token_id, actor_ref="member:m-a")
        views: tuple[TokenView, ...] = await tokens.list_tokens(session)
        assert [view.id for view in views] == [bound.token_id, machine.token_id, legacy.token_id]
        assert views[0].member_handle == "alice"
        assert views[0].scopes == ("read", "write")
        assert views[0].revoked_at is None
        assert views[1].member_handle is None
        assert views[1].revoked_at is not None
        assert all(bound.token not in repr(view) for view in views)
        created: list[AuditRecordRow] = await _audit_rows(session, "token.created")
        assert [(row.actor_kind, row.actor_ref) for row in created] == [
            ("member", "member:m-a"),
            ("token", "token:t-root"),
            ("system", "system:tokens"),
        ]
        revoked: list[AuditRecordRow] = await _audit_rows(session, "token.revoked")
        assert [(row.actor_kind, row.actor_ref) for row in revoked] == [("member", "member:m-a")]
        for row in created + revoked:
            assert bound.token not in row.details_json
            assert machine.token not in row.details_json
    await harness.close()
