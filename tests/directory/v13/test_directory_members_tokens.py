"""V1.3 actor display, member administration and self-token behavior."""

from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Headers
from httpx import Response as HttpxResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.common import ApiContext, install_error_handlers
from telescreen.api.routers.users import UsersRouterDeps, build_users_router
from telescreen.audit.service import AuditLogService
from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.json import dump_json, parse_json
from telescreen.core.vocabulary import (
    ApiScope,
    ErrorCode,
    ExceptionDuration,
    IssueStatus,
    RemediationAction,
    Role,
)
from telescreen.db.models import ApiTokenRow, AuditRecordRow, IssueRow, MemberRow, TeamRow
from telescreen.directory.actors import DirectoryActorDirectory
from telescreen.directory.members import MemberAdminService
from telescreen.directory.repository import DirectoryRepository
from telescreen.directory.roles import RoleService
from telescreen.directory.tokens import CreatedToken, TokenService, TokenView
from telescreen.masking.redact import Redactor
from telescreen.ports.directory import ActorDisplay
from telescreen.ports.issues import RemediationReceipt
from tests.issues.v13_lifecycle.test_archive import NOW, RecordingAudit, _issue
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import github_token


@dataclass
class FakeRemediation:
    """Collect issue release calls for member deactivation tests."""

    issue_ids: list[str]

    async def release_to_team(self, session: AsyncSession, issue_id: str, actor_ref: str) -> None:
        """Record a release request without mutating the issue row."""
        del session
        assert actor_ref == "system:deactivation"
        self.issue_ids.append(issue_id)

    async def request_verification(
        self,
        session: AsyncSession,
        issue_id: str,
        action: RemediationAction,
        include_linked: bool,
        actor_ref: str,
        note: str | None,
    ) -> RemediationReceipt:
        """Unused protocol method for this test fake."""
        del session, issue_id, action, include_linked, actor_ref, note
        raise AssertionError("unexpected verification request")

    async def request_exception(
        self,
        session: AsyncSession,
        issue_id: str,
        reason: str,
        duration: ExceptionDuration,
        actor_ref: str,
        is_admin: bool,
    ) -> str:
        """Unused protocol method for this test fake."""
        del session, issue_id, reason, duration, actor_ref, is_admin
        raise AssertionError("unexpected exception request")


def _member(member_id: str, handle: str, role: Role, active: bool = True) -> MemberRow:
    """Create a member row for the in-memory test database."""
    return MemberRow(
        id=member_id,
        handle=handle,
        display_name=handle.title(),
        email=None,
        slack_user_id=None,
        active=active,
        created_at=NOW,
        updated_at=NOW,
        role=role.value,
    )


@pytest.mark.asyncio
async def test_actor_display_preserves_order_and_handles_unknown_refs() -> None:
    """Actor display resolves member/team/token refs in input order and labels unknowns."""
    harness: SqliteHarness = await make_sqlite_harness()
    async with harness.sessions.begin() as session:
        session.add(_member("m-a", "alice", Role.ADMIN, active=False))
        session.add(
            TeamRow(id="t-a", slug="security", name="Security", created_at=NOW, updated_at=NOW)
        )
        session.add(
            ApiTokenRow(
                id="token-a",
                label="automation",
                token_hash="a" * 64,
                scopes="read",
                member_id="m-a",
                created_at=NOW,
                revoked_at=None,
            )
        )
        refs: tuple[str, ...] = (
            "member:m-a",
            "member:alice",
            "team:security",
            "token:token-a",
            "system:scanner",
            "bogus",
        )
        displays: tuple[ActorDisplay, ...] = await DirectoryActorDirectory(
            DirectoryRepository()
        ).display(session, refs)
    assert len(displays) == len(refs)
    assert [display.kind.value for display in displays] == [
        "member",
        "member",
        "team",
        "token",
        "system",
        "unknown",
    ]
    assert displays[0].active is False
    assert displays[2].display_name == "Security"
    assert displays[4].display_name == "Telescreen"
    assert displays[5].display_name == "Unknown"
    await harness.close()


@pytest.mark.asyncio
async def test_deactivation_protects_last_admin_and_releases_only_open_issues() -> None:
    """Deactivation releases unresolved and archived issues, preserving resolved work."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(NOW)
    audit: RecordingAudit = RecordingAudit([])
    remediation: FakeRemediation = FakeRemediation([])
    service: MemberAdminService = MemberAdminService(
        DirectoryRepository(), RoleService(audit, clock), remediation, audit, clock
    )
    async with harness.sessions.begin() as session:
        session.add(_member("m-admin", "root", Role.ADMIN))
        session.add(_member("m-other", "other", Role.ADMIN))
        open_issue: IssueRow = _issue("open-assigned", 1, IssueStatus.UNRESOLVED)
        archived: IssueRow = _issue("archived-assigned", 2, IssueStatus.ARCHIVED)
        resolved: IssueRow = _issue("resolved-assigned", 3, IssueStatus.RESOLVED)
        for issue in (open_issue, archived, resolved):
            issue.assignee_member_id = "m-admin"
            session.add(issue)
        await service.deactivate(session, "root", "member:m-other")
    assert set(remediation.issue_ids) == {"open-assigned", "archived-assigned"}
    async with harness.sessions.begin() as session:
        reactivated: MemberRow = await service.reactivate(session, "root", "member:m-other")
        assert reactivated.active
    async with harness.sessions.begin() as session:
        session.add(_member("m-last", "last", Role.ADMIN))
        other: MemberRow | None = await session.get(MemberRow, "m-other")
        root: MemberRow | None = await session.get(MemberRow, "m-admin")
        assert other is not None
        assert root is not None
        other.active = False
        root.active = False
        with pytest.raises(TelescreenError) as failure:
            await service.deactivate(session, "last", "member:m-other")
        assert failure.value.code is ErrorCode.LAST_ADMIN
        last: MemberRow | None = await session.scalar(
            select(MemberRow).where(MemberRow.handle == "last")
        )
        assert last is not None and last.active
    await harness.close()


@pytest.mark.asyncio
async def test_member_edit_masks_sensitive_input_and_audits_field_names_only() -> None:
    """Member edits reject detected secrets and audit field names without edited values."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(NOW)
    audit: AuditLogService = AuditLogService(clock, Redactor.default())
    service: MemberAdminService = MemberAdminService(
        DirectoryRepository(), RoleService(audit, clock), None, audit, clock
    )
    sample: str = github_token().value
    async with harness.sessions.begin() as session:
        session.add(_member("m-a", "alice", Role.ADMIN))
        with pytest.raises(TelescreenError) as invalid_email:
            await service.update(session, "alice", None, "invalid email", None, "member:m-a")
        assert invalid_email.value.code is ErrorCode.VALIDATION_ERROR
        with pytest.raises(TelescreenError) as invalid_slack:
            await service.update(session, "alice", None, None, "Ubad", "member:m-a")
        assert invalid_slack.value.code is ErrorCode.VALIDATION_ERROR
        with pytest.raises(TelescreenError) as failure:
            await service.update(session, "alice", sample, None, None, "member:m-a")
        assert failure.value.code is ErrorCode.VALIDATION_ERROR
        row: MemberRow | None = await session.scalar(
            select(MemberRow).where(MemberRow.handle == "alice")
        )
        assert row is not None and row.display_name == "Alice"
        await service.update(
            session, "alice", "Alice Morgan", "alice@example.test", "U012345678", "member:m-a"
        )
        records: tuple[AuditRecordRow, ...] = tuple(
            (
                await session.scalars(
                    select(AuditRecordRow).where(AuditRecordRow.action == "member.updated")
                )
            ).all()
        )
        details: str = dump_json(parse_json(records[0].details_json))
        assert "display_name" in details and "email" in details and "slack_user_id" in details
        assert "alice@example.test" not in details
    await harness.close()


@pytest.mark.asyncio
async def test_self_tokens_are_bound_scoped_and_owned() -> None:
    """Role limits token scopes and token ownership checks hide other members' ids."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(NOW)
    audit: RecordingAudit = RecordingAudit([])
    tokens: TokenService = TokenService(audit, clock)
    async with harness.sessions.begin() as session:
        session.add(_member("m-viewer", "viewer", Role.VIEWER))
        session.add(_member("m-other", "other", Role.ADMIN))
        session.add(_member("m-admin", "admin", Role.ADMIN))
        token: CreatedToken = await tokens.create_for_member(
            session, "m-viewer", Role.VIEWER, "personal", frozenset((ApiScope.READ,))
        )
        stored: ApiTokenRow | None = await session.get(ApiTokenRow, token.token_id)
        assert stored is not None and stored.member_id == "m-viewer"
        with pytest.raises(TelescreenError) as failure:
            await tokens.create_for_member(
                session, "m-viewer", Role.VIEWER, "invalid", frozenset((ApiScope.WRITE,))
            )
        assert failure.value.code is ErrorCode.VALIDATION_ERROR
        other: CreatedToken = await tokens.create_for_member(
            session, "m-other", Role.ADMIN, "other", frozenset((ApiScope.READ,))
        )
        narrow_admin_token: CreatedToken = await tokens.create(
            session,
            "narrow-admin",
            frozenset((ApiScope.READ,)),
            "m-admin",
        )
        before_denied_create: int = len((await session.scalars(select(ApiTokenRow))).all())
        with pytest.raises(TelescreenError) as escalated:
            await tokens.create_for_member(
                session,
                "m-admin",
                Role.ADMIN,
                "must-not-escalate",
                frozenset((ApiScope.ADMIN,)),
                narrow_admin_token.token_id,
            )
        assert escalated.value.code is ErrorCode.VALIDATION_ERROR
        after_denied_create: int = len((await session.scalars(select(ApiTokenRow))).all())
        assert after_denied_create == before_denied_create
        read_child: CreatedToken = await tokens.create_for_member(
            session,
            "m-admin",
            Role.ADMIN,
            "read-child",
            frozenset((ApiScope.READ,)),
            narrow_admin_token.token_id,
        )
        read_child_row: ApiTokenRow | None = await session.get(ApiTokenRow, read_child.token_id)
        assert read_child_row is not None and read_child_row.scopes == ApiScope.READ.value
        oidc_admin_token: CreatedToken = await tokens.create_for_member(
            session,
            "m-admin",
            Role.ADMIN,
            "oidc-admin",
            frozenset((ApiScope.ADMIN,)),
        )
        oidc_admin_row: ApiTokenRow | None = await session.get(
            ApiTokenRow, oidc_admin_token.token_id
        )
        assert oidc_admin_row is not None and oidc_admin_row.scopes == ApiScope.ADMIN.value
        listed: tuple[TokenView, ...] = await tokens.list_for_member(session, "m-viewer")
        assert [item.id for item in listed] == [token.token_id]
        with pytest.raises(TelescreenError) as missing:
            await tokens.revoke_for_member(session, "m-viewer", other.token_id)
        assert missing.value.code is ErrorCode.NOT_FOUND
    await harness.close()


@pytest.mark.asyncio
async def test_read_scoped_admin_token_cannot_mint_admin_scope_over_http() -> None:
    """A narrow admin token cannot use the self-token route to escalate its scopes."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(NOW)
    audit: RecordingAudit = RecordingAudit([])
    tokens: TokenService = TokenService(audit, clock)
    async with harness.sessions.begin() as session:
        session.add(_member("m-admin", "admin", Role.ADMIN))
        caller: CreatedToken = await tokens.create(
            session, "read-only", frozenset((ApiScope.READ,)), "m-admin"
        )

    context: ApiContext = ApiContext(harness.sessions, clock, audit)
    app: FastAPI = FastAPI()
    install_error_handlers(app)
    app.include_router(
        build_users_router(UsersRouterDeps(context, RoleService(audit, clock), tokens, True))
    )
    transport: ASGITransport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers: Headers = Headers((("Authorization", f"Bearer {caller.token}"),))
        before_denied_create: int
        async with harness.sessions() as session:
            before_denied_create = len((await session.scalars(select(ApiTokenRow))).all())
        denied: HttpxResponse = await client.post(
            "/api/auth/me/tokens",
            headers=headers,
            json={"label": "escalation", "scopes": [ApiScope.ADMIN.value]},
        )
        assert denied.status_code == 422
        async with harness.sessions() as session:
            after_denied_create: int = len((await session.scalars(select(ApiTokenRow))).all())
        assert after_denied_create == before_denied_create
        allowed: HttpxResponse = await client.post(
            "/api/auth/me/tokens",
            headers=headers,
            json={"label": "read-child", "scopes": [ApiScope.READ.value]},
        )
        assert allowed.status_code == 201
        token_id: str = str(allowed.json()["id"])
        async with harness.sessions() as session:
            child: ApiTokenRow | None = await session.get(ApiTokenRow, token_id)
        assert child is not None and child.member_id == "m-admin"
        assert child.scopes == ApiScope.READ.value
    await harness.close()
