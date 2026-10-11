"""v1.2 OIDC member provisioning tests over the SQLite harness."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.audit.service import AuditLogService
from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.oidc_settings import OidcTeamMapping
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import (
    ApiScope,
    ErrorCode,
    IdentityKind,
    IdentityOrigin,
    MembershipOrigin,
    OidcProvisioning,
    Role,
    SignInProvisioning,
)
from telescreen.db.base import Base
from telescreen.db.models import AuditRecordRow, MemberIdentityRow, MemberRow, TeamMembershipRow
from telescreen.directory import (
    CreatedToken,
    DirectoryProvisioner,
    DirectoryService,
    RoleService,
    TokenService,
)
from telescreen.directory.provisioning import handle_base
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.identity import MemberAuth, SignInRequest, SignInResult
from tests.support.db import SqliteHarness, make_sqlite_harness

NOW: datetime = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
ISSUER: str = "https://idp.example.test/realms/telescreen"
SUBJECT: str = "kc-sub-7f3e9a1b-raw-subject-value"
OTHER_SUBJECT: str = "kc-sub-0c4d2e88-other-subject-value"
ALICE_EMAIL: str = "Alice.Idp@Example.test"


@dataclass
class World:
    """Directory services sharing one harness, clock and audit log."""

    harness: SqliteHarness
    clock: FakeClock
    fingerprinter: Fingerprinter
    directory: DirectoryService
    provisioner: DirectoryProvisioner
    tokens: TokenService


async def _world() -> World:
    """Build the directory services with a real fingerprinter and audit log."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(NOW)
    audit: AuditLogService = AuditLogService(clock, Redactor.default())
    fingerprinter: Fingerprinter = Fingerprinter.from_secret(
        SecretValue("0123456789abcdef0123456789abcdef")
    )
    roles: RoleService = RoleService(audit, clock)
    return World(
        harness,
        clock,
        fingerprinter,
        DirectoryService(audit, clock, fingerprinter=fingerprinter),
        DirectoryProvisioner(fingerprinter, roles, audit, clock),
        TokenService(audit, clock),
    )


def _request(
    subject: str = SUBJECT,
    email: str | None = ALICE_EMAIL,
    email_verified: bool = True,
    role: Role = Role.RESPONDER,
    provisioning: OidcProvisioning = OidcProvisioning.LINK,
    preferred_username: str | None = "alice",
    groups: tuple[str, ...] = (),
    team_map: tuple[OidcTeamMapping, ...] = (),
) -> SignInRequest:
    """Build a mapped sign-in request."""
    return SignInRequest(
        issuer=ISSUER,
        subject=subject,
        email=email,
        email_verified=email_verified,
        preferred_username=preferred_username,
        display_name="Alice Idp",
        role=role,
        groups=groups,
        provisioning=provisioning,
        team_map=team_map,
    )


async def _actions(session: AsyncSession) -> list[str]:
    """Return audit actions in sequence order."""
    rows: Sequence[AuditRecordRow] = (
        await session.scalars(select(AuditRecordRow).order_by(AuditRecordRow.seq))
    ).all()
    return [row.action for row in rows]


async def _database_text(session: AsyncSession) -> str:
    """Concatenate every row of every table as text."""
    parts: list[str] = []
    for table in Base.metadata.sorted_tables:
        rows: Sequence[Row[tuple[str, ...]]] = (await session.execute(select(table))).all()
        parts.extend(str(tuple(row)) for row in rows)
    return "\n".join(parts)


def test_handle_base_slugifies_claims() -> None:
    """Handles are lowercase [a-z0-9-] slugs of at most 64 chars with a fallback."""
    assert handle_base("Alice.Smith") == "alice-smith"
    assert handle_base("  --Bob__Jones--  ") == "bob-jones"
    assert handle_base("***") == "user"
    assert handle_base("") == "user"
    assert len(handle_base("x" * 100)) == 64


@pytest.mark.asyncio
async def test_link_by_verified_email_then_existing_sign_in() -> None:
    """A verified email links once; later sign-ins resolve the keyed identity."""
    world: World = await _world()
    async with world.harness.sessions.begin() as session:
        member: MemberRow = await world.directory.create_member(
            session, "alice", "Alice", "alice.idp@example.test", None
        )
        linked: SignInResult = await world.provisioner.sign_in(session, _request())
        assert linked.provisioning is SignInProvisioning.LINKED
        assert linked.role_changed is True
        assert linked.member == MemberAuth(member.id, "alice", "Alice", Role.RESPONDER, True)
        assert member.role == Role.RESPONDER.value
        assert member.last_sign_in_at is not None
        identity: MemberIdentityRow | None = await session.scalar(
            select(MemberIdentityRow).where(
                MemberIdentityRow.kind == IdentityKind.OIDC_SUBJECT.value
            )
        )
        assert identity is not None
        assert identity.member_id == member.id
        assert identity.origin == IdentityOrigin.OIDC.value
        assert len(identity.identity_key) == 64
        assert SUBJECT not in identity.identity_key
        assert ALICE_EMAIL.lower() not in identity.identity_key
        assert await _actions(session) == [
            "directory.member_created",
            "identity.linked",
            "role.changed",
        ]
        world.clock.set(datetime(2026, 10, 11, 13, 0, tzinfo=UTC))
        again: SignInResult = await world.provisioner.sign_in(session, _request())
        assert again.provisioning is SignInProvisioning.EXISTING
        assert again.role_changed is False
        assert member.last_sign_in_at == datetime(2026, 10, 11, 13, 0, tzinfo=UTC)
        found: MemberAuth | None = await world.provisioner.member_for_identity(
            session, ISSUER, SUBJECT
        )
        assert found == MemberAuth(member.id, "alice", "Alice", Role.RESPONDER, True)
        assert await world.provisioner.member_for_identity(session, ISSUER, OTHER_SUBJECT) is None
        assert (
            await world.provisioner.member_for_identity(session, "https://other", SUBJECT) is None
        )
        demoted: SignInResult = await world.provisioner.sign_in(session, _request(role=Role.VIEWER))
        assert demoted.role_changed is True
        assert demoted.member.role is Role.VIEWER
        member.active = False
        inactive: MemberAuth | None = await world.provisioner.member_for_identity(
            session, ISSUER, SUBJECT
        )
        assert inactive is not None and inactive.active is False
        with pytest.raises(TelescreenError) as forbidden:
            await world.provisioner.sign_in(session, _request())
        assert forbidden.value.code is ErrorCode.FORBIDDEN
    await world.harness.close()


@pytest.mark.asyncio
async def test_link_rejects_missing_unverified_ambiguous_and_conflicting_emails() -> None:
    """Link mode returns NOT_PROVISIONED or IDENTITY_CONFLICT without provisioning."""
    world: World = await _world()
    async with world.harness.sessions.begin() as session:
        with pytest.raises(TelescreenError) as no_match:
            await world.provisioner.sign_in(session, _request())
        assert no_match.value.code is ErrorCode.NOT_PROVISIONED
        await world.directory.create_member(session, "alice", "Alice", ALICE_EMAIL, None)
        with pytest.raises(TelescreenError) as unverified:
            await world.provisioner.sign_in(session, _request(email_verified=False))
        assert unverified.value.code is ErrorCode.NOT_PROVISIONED
        first: SignInResult = await world.provisioner.sign_in(session, _request())
        assert first.provisioning is SignInProvisioning.LINKED
        with pytest.raises(TelescreenError) as conflict:
            await world.provisioner.sign_in(session, _request(subject=OTHER_SUBJECT))
        assert conflict.value.code is ErrorCode.IDENTITY_CONFLICT
        await world.directory.create_member(session, "bob", "Bob", "bob@example.test", None)
        await world.directory.create_member(session, "bobby", "Bobby", None, None)
        bobby: MemberRow | None = await session.scalar(
            select(MemberRow).where(MemberRow.handle == "bobby")
        )
        assert bobby is not None
        bobby.email = "BOB@example.test"
        with pytest.raises(TelescreenError) as ambiguous:
            await world.provisioner.sign_in(
                session,
                _request(
                    subject="bob-sub",
                    email="bob@example.test",
                    provisioning=OidcProvisioning.CREATE,
                ),
            )
        assert ambiguous.value.code is ErrorCode.NOT_PROVISIONED
        assert "identity.linked" in await _actions(session)
        assert (await _actions(session)).count("identity.linked") == 1
    await world.harness.close()


@pytest.mark.asyncio
async def test_create_provisions_members_with_suffixed_handles() -> None:
    """Create mode provisions a member, its identities and audits; taken handles get -2."""
    world: World = await _world()
    async with world.harness.sessions.begin() as session:
        created: SignInResult = await world.provisioner.sign_in(
            session, _request(provisioning=OidcProvisioning.CREATE, role=Role.ADMIN)
        )
        assert created.provisioning is SignInProvisioning.CREATED
        assert created.member.handle == "alice"
        assert created.member.display_name == "Alice Idp"
        assert created.member.role is Role.ADMIN
        member: MemberRow | None = await session.scalar(
            select(MemberRow).where(MemberRow.handle == "alice")
        )
        assert member is not None
        assert member.email == ALICE_EMAIL
        kinds: set[str] = set(
            (
                await session.scalars(
                    select(MemberIdentityRow.kind).where(MemberIdentityRow.member_id == member.id)
                )
            ).all()
        )
        assert kinds == {IdentityKind.OIDC_SUBJECT.value, IdentityKind.EMAIL.value}
        second: SignInResult = await world.provisioner.sign_in(
            session,
            _request(
                subject=OTHER_SUBJECT,
                email="alice.other@example.test",
                email_verified=False,
                provisioning=OidcProvisioning.CREATE,
            ),
        )
        assert second.member.handle == "alice-2"
        other: MemberRow | None = await session.scalar(
            select(MemberRow).where(MemberRow.handle == "alice-2")
        )
        assert other is not None
        assert other.email is None
        third: SignInResult = await world.provisioner.sign_in(
            session,
            _request(
                subject="third-sub",
                email="carol.x@example.test",
                preferred_username=None,
                provisioning=OidcProvisioning.CREATE,
            ),
        )
        assert third.member.handle == "carol-x"
        actions: list[str] = await _actions(session)
        assert actions.count("member.provisioned") == 3
        assert actions.count("identity.linked") == 3
        records: Sequence[AuditRecordRow] = (await session.scalars(select(AuditRecordRow))).all()
        for record in records:
            assert ALICE_EMAIL.lower() not in record.details_json.lower()
            assert "carol.x" not in record.details_json
            assert SUBJECT not in record.details_json
    await world.harness.close()


@pytest.mark.asyncio
async def test_team_sync_adds_and_removes_only_idp_memberships() -> None:
    """Mapped groups add idp memberships, absence removes them, manual rows stay."""
    world: World = await _world()
    team_map: tuple[OidcTeamMapping, ...] = (
        OidcTeamMapping("security", "secops"),
        OidcTeamMapping("dev", "devs"),
        OidcTeamMapping("ghost", "missing-team"),
    )
    async with world.harness.sessions.begin() as session:
        member: MemberRow = await world.directory.create_member(
            session, "alice", "Alice", ALICE_EMAIL, None
        )
        await world.directory.create_team(session, "secops", "SecOps", None, None)
        await world.directory.create_team(session, "devs", "Devs", None, None)
        await world.directory.set_team_members(session, "devs", ("alice",))
        await world.provisioner.sign_in(
            session, _request(groups=("security", "ghost"), team_map=team_map)
        )
        rows: list[TeamMembershipRow] = list(
            (
                await session.scalars(
                    select(TeamMembershipRow).where(TeamMembershipRow.member_id == member.id)
                )
            ).all()
        )
        assert sorted(row.origin for row in rows) == [
            MembershipOrigin.IDP.value,
            MembershipOrigin.MANUAL.value,
        ]
        assert (await _actions(session)).count("team_membership.synced") == 1
        await world.provisioner.sign_in(session, _request(groups=(), team_map=team_map))
        remaining: list[TeamMembershipRow] = list(
            (
                await session.scalars(
                    select(TeamMembershipRow).where(TeamMembershipRow.member_id == member.id)
                )
            ).all()
        )
        assert [row.origin for row in remaining] == [MembershipOrigin.MANUAL.value]
        await world.provisioner.sign_in(session, _request(groups=("dev",), team_map=team_map))
        assert (await _actions(session)).count("team_membership.synced") == 2
        await world.provisioner.sign_in(session, _request(groups=("security",)))
        assert (await _actions(session)).count("team_membership.synced") == 2
    await world.harness.close()


@pytest.mark.asyncio
async def test_database_never_contains_subject_or_token_values() -> None:
    """No table text contains the raw subject or a minted token value."""
    world: World = await _world()
    async with world.harness.sessions.begin() as session:
        result: SignInResult = await world.provisioner.sign_in(
            session, _request(provisioning=OidcProvisioning.CREATE)
        )
        token: CreatedToken = await world.tokens.create(
            session,
            "alice-cli",
            frozenset((ApiScope.READ,)),
            result.member.member_id,
            actor_ref=f"member:{result.member.member_id}",
        )
        text: str = await _database_text(session)
        assert "alice-cli" in text
        assert SUBJECT not in text
        assert ISSUER + "\0" + SUBJECT not in text
        assert token.token not in text
    await world.harness.close()
