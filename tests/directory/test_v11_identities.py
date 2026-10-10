"""Directory identity and owner resolution behavior tests."""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import ErrorCode, IdentityKind, IdentityOrigin
from telescreen.db.models import MemberIdentityRow, MemberRow, TeamRow
from telescreen.directory.identities import IdentityService, IdentityView
from telescreen.directory.owners import DirectoryOwnerResolver
from telescreen.directory.service import DirectoryService
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.ports.audit import AuditEntry
from telescreen.ports.collectors import OwnerIdentity
from tests.support.db import SqliteHarness, make_sqlite_harness


@dataclass
class RecordingAudit:
    """Capture directory audit entries without storing identity values."""

    entries: list[AuditEntry]

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Record one audit entry in memory."""
        del session
        self.entries.append(entry)
        return len(self.entries)


def _fingerprinter() -> Fingerprinter:
    """Create a deterministic test fingerprinter."""
    return Fingerprinter.from_secret(SecretValue("identity-test-key-material-32-bytes"))


@pytest.mark.asyncio
async def test_identity_lifecycle_and_owner_directory_are_keyed() -> None:
    """Derive and manage keyed identities without persisting or returning values."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 10, tzinfo=UTC))
    audit: RecordingAudit = RecordingAudit([])
    fingerprinter: Fingerprinter = _fingerprinter()
    directory: DirectoryService = DirectoryService(audit, clock, fingerprinter=fingerprinter)
    identities: IdentityService = IdentityService(fingerprinter, audit, clock)
    owners: DirectoryOwnerResolver = DirectoryOwnerResolver()
    async with harness.sessions.begin() as session:
        member: MemberRow = await directory.create_member(
            session, "alice", "Alice", "Alice@Example.com", "U-ALICE"
        )
        team: TeamRow = await directory.create_team(session, "responders", "Responders", None, None)
        await directory.set_team_members(session, "responders", ("alice",))
        await directory.set_team_lead(session, "responders", "alice", "member:admin")
        assert await owners.member_ref(session, member.id) == "member:alice"
        assert await owners.team_ref(session, team.id) == "team:responders"
        assert await owners.team_refs_of(session, member.id) == ("team:responders",)
        assert await owners.team_lead(session, "responders") == "member:alice"
        email_ref: str = fingerprinter.owner_ref(
            OwnerIdentity(IdentityKind.EMAIL, "alice@example.com")
        )
        assert await owners.member_for_owner(session, email_ref) == "member:alice"
        assert await owners.member_for_owner(session, "email:malformed") is None
        derived: list[MemberIdentityRow] = list(
            (await session.scalars(select(MemberIdentityRow))).all()
        )
        assert len(derived) == 2
        assert {row.origin for row in derived} == {
            IdentityOrigin.MEMBER_EMAIL.value,
            IdentityOrigin.MEMBER_SLACK.value,
        }
        assert "Alice@Example.com" not in str(derived)
        assert "U-ALICE" not in str(derived)
        with pytest.raises(TelescreenError) as derived_remove:
            await identities.remove(session, "alice", derived[0].id, "member:admin")
        assert derived_remove.value.code is ErrorCode.CONFLICT
        manual: MemberIdentityRow = await identities.add(
            session, "alice", IdentityKind.GITHUB_LOGIN, "AliceGH", "member:admin"
        )
        assert manual.origin == IdentityOrigin.MANUAL.value
        with pytest.raises(TelescreenError) as duplicate:
            await identities.add(
                session, "alice", IdentityKind.GITHUB_LOGIN, "alicegh", "member:admin"
            )
        assert duplicate.value.code is ErrorCode.CONFLICT
        views: tuple[IdentityView, ...] = await identities.list(session, "alice")
        assert len(views) == 3
        assert all(len(view.key_prefix) == 8 for view in views)
        assert all(not hasattr(view, "value") for view in views)
        await identities.remove(session, "alice", manual.id, "member:admin")
        assert len(await identities.list(session, "alice")) == 2
        with pytest.raises(TelescreenError) as unknown_lead:
            await directory.set_team_lead(session, "responders", "missing", "member:admin")
        assert unknown_lead.value.code is ErrorCode.NOT_FOUND
        member.active = False
        assert await owners.member_ref(session, member.id) is None
        assert await owners.member_for_owner(session, email_ref) is None
    assert [entry.action for entry in audit.entries].count("identity.added") == 1
    assert [entry.action for entry in audit.entries].count("identity.removed") == 1
    assert all("AliceGH" not in str(entry) for entry in audit.entries)
    await harness.close()


@pytest.mark.asyncio
async def test_identity_service_rejects_empty_and_unknown_handles() -> None:
    """Identity validation returns static errors for empty values and unknown handles."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 10, tzinfo=UTC))
    service: IdentityService = IdentityService(_fingerprinter(), RecordingAudit([]), clock)
    async with harness.sessions.begin() as session:
        with pytest.raises(TelescreenError) as unknown:
            await service.add(session, "missing", IdentityKind.EMAIL, "x@example.invalid", "admin")
        assert unknown.value.code is ErrorCode.NOT_FOUND
        session.add(
            MemberRow(
                id="member-1",
                handle="alice",
                display_name="Alice",
                email=None,
                slack_user_id=None,
                active=True,
                created_at=clock.now(),
                updated_at=clock.now(),
            )
        )
        with pytest.raises(TelescreenError) as empty:
            await service.add(session, "alice", IdentityKind.EMAIL, "  ", "admin")
        assert empty.value.code is ErrorCode.VALIDATION_ERROR
    await harness.close()
