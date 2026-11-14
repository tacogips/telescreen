"""Directory replica persistence, authentication and directory port behavior."""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Scope

from telescreen.api.common import ApiContext
from telescreen.api.common.auth import authenticate
from telescreen.audit.chain import INVENTORY_AUDIT_MODELS
from telescreen.audit.export import export_chain
from telescreen.audit.service import AuditLogService
from telescreen.audit.verify import ChainVerification, verify_chain
from telescreen.collectors.repository import CollectorRepository
from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.json import json_object
from telescreen.core.vocabulary import ActorKind, ErrorCode, IdentityKind, PrincipalKind, Role
from telescreen.db.models import InventoryDirectoryEntryRow
from telescreen.db.models.service_audit import InventoryAuditRecordRow
from telescreen.detection import RuleDetector
from telescreen.directory.actors import DirectoryActorDirectory
from telescreen.directory.inventory_actors import InventoryActorDirectory
from telescreen.directory.owners import DirectoryOwnerResolver
from telescreen.directory.resolver import DirectoryRecipientResolver
from telescreen.issues.asset_owner import IssueAssetOwnerResolver
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry
from telescreen.ports.collectors import OwnerIdentity
from telescreen.ports.directory import ResolvedRecipients
from telescreen.ports.directory_projection import (
    DirectoryEntity,
    IdentityEntity,
)
from telescreen.ports.directory_projection import (
    DirectoryEntityKind as EntityKind,
)
from telescreen.ports.identity import (
    MemberAuth,
    MemberProvisioner,
    ProviderMetadata,
    SignInRequest,
    SignInResult,
    VerifiedToken,
)
from telescreen.ports.identity_context import ActorDisplay
from telescreen.ports.principal import Principal
from telescreen.replica.auth import ReplicaAuthenticator
from telescreen.replica.directory import (
    ReplicaActorDirectory,
    ReplicaOwnerDirectory,
    ReplicaRecipientResolver,
)
from telescreen.replica.rules import ReplicaOwnershipRules
from telescreen.replica.store import DirectoryReplica
from tests.replica.fixture import (
    MEMBER_ID,
    TEAM_ID,
    TOKEN_VALUE,
    directory_entities,
    populate_main_directory,
)
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import Sample, assert_no_leaks, github_token


async def _replicate(harness: SqliteHarness, replica: DirectoryReplica) -> None:
    async with harness.sessions.begin() as session:
        for entity in directory_entities():
            await replica.apply(session, entity)


@pytest.mark.asyncio
async def test_revision_guard_and_encoded_tombstone_block_older_entity() -> None:
    harness: SqliteHarness = await make_sqlite_harness(store=None)
    replica: DirectoryReplica = DirectoryReplica(InventoryDirectoryEntryRow)
    original: DirectoryEntity = directory_entities()[0]
    async with harness.sessions.begin() as session:
        assert await replica.apply(session, original)
        newer: DirectoryEntity = DirectoryEntity(original.kind, original.id, 3, False, None)
        assert await replica.apply(session, newer)
        assert not await replica.apply(session, original)
    async with harness.sessions() as session:
        assert await replica.get(session, EntityKind.MEMBER, MEMBER_ID) is None
        assert await replica.watermark(session) == 3
        row: InventoryDirectoryEntryRow | None = await session.get(
            InventoryDirectoryEntryRow, (EntityKind.MEMBER.value, MEMBER_ID)
        )
        assert row is not None
        assert '"present":false' in row.payload_json
    await harness.close()


@pytest.mark.asyncio
async def test_replica_authenticates_token_and_directory_ports_match_projection() -> None:
    harness: SqliteHarness = await make_sqlite_harness()
    replica: DirectoryReplica = DirectoryReplica(InventoryDirectoryEntryRow)
    await _replicate(harness, replica)
    async with harness.sessions.begin() as session:
        await populate_main_directory(session)
    scope: Scope = {
        "type": "http",
        "headers": [(b"authorization", f"Bearer {TOKEN_VALUE}".encode())],
    }
    request: Request = Request(scope)
    auth: ReplicaAuthenticator = ReplicaAuthenticator(harness.sessions, replica, None, None)
    principal: Principal = await auth(request)
    assert principal.kind is PrincipalKind.TOKEN
    assert principal.member_id == MEMBER_ID

    recipients: ReplicaRecipientResolver = ReplicaRecipientResolver(replica)
    actors: ReplicaActorDirectory = ReplicaActorDirectory(replica)
    owners: ReplicaOwnerDirectory = ReplicaOwnerDirectory(replica)
    rules: ReplicaOwnershipRules = ReplicaOwnershipRules(replica)
    async with harness.sessions() as session:
        main_recipients: DirectoryRecipientResolver = DirectoryRecipientResolver()
        main_owners: DirectoryOwnerResolver = DirectoryOwnerResolver()
        main_actors: InventoryActorDirectory = InventoryActorDirectory(
            DirectoryActorDirectory(), CollectorRepository()
        )
        member_recipients: ResolvedRecipients = await recipients.resolve(session, "member:alice")
        assert member_recipients == await main_recipients.resolve(session, "member:alice")
        team_recipients: ResolvedRecipients = await recipients.resolve(session, "team:responders")
        assert team_recipients == await main_recipients.resolve(session, "team:responders")
        assert team_recipients.emails == (
            "alice@example.test",
            "team@example.test",
        )
        assert await recipients.is_member_of(session, MEMBER_ID, "team:responders")
        assert (await recipients.assignee_of(session, "team:responders")).team_id == TEAM_ID
        displays: tuple[ActorDisplay, ...] = await actors.display(
            session,
            (
                "member:alice",
                "team:responders",
                "token:00000000-0000-0000-0000-000000000003",
                "system:test",
                "collector:00000000-0000-0000-0000-000000000005",
                "unknown:x",
            ),
        )
        assert displays == await main_actors.display(
            session,
            (
                "member:alice",
                "team:responders",
                "token:00000000-0000-0000-0000-000000000003",
                "system:test",
                "collector:00000000-0000-0000-0000-000000000005",
                "unknown:x",
            ),
        )
        owner_ref: str = f"oidc_subject:{'a' * 64}"
        assert await owners.member_ref(session, MEMBER_ID) == await main_owners.member_ref(
            session, MEMBER_ID
        )
        assert await owners.team_ref(session, TEAM_ID) == await main_owners.team_ref(
            session, TEAM_ID
        )
        assert await owners.team_refs_of(session, MEMBER_ID) == await main_owners.team_refs_of(
            session, MEMBER_ID
        )
        assert await owners.member_for_owner(
            session, owner_ref
        ) == await main_owners.member_for_owner(session, owner_ref)
        assert await owners.team_lead(session, "responders") == await main_owners.team_lead(
            session, "responders"
        )
        main_rule_fallback: str | None = await IssueAssetOwnerResolver(main_owners).fallback_owner(
            session, "github", "resource-key", "org/repo"
        )
        assert await rules.match(session, "github", "org/repo") == main_rule_fallback
    await harness.close()


@pytest.mark.asyncio
async def test_inventory_audit_chain_parameterization_and_export() -> None:
    harness: SqliteHarness = await make_sqlite_harness(store=None)
    audit: AuditLogService = AuditLogService(
        FakeClock(datetime(2026, 10, 16, tzinfo=UTC)),
        Redactor(RuleDetector()),
        INVENTORY_AUDIT_MODELS,
    )
    sample: Sample = github_token()
    async with harness.sessions.begin() as session:
        for number in range(1, 4):
            await audit.append(
                session,
                AuditEntry(
                    ActorKind.SYSTEM,
                    "system:test",
                    "replica.test",
                    "member",
                    MEMBER_ID,
                    f"entry {number} {sample.value}" if number == 1 else f"entry {number}",
                    json_object(()),
                ),
            )
    async with harness.sessions() as session:
        assert await verify_chain(session, INVENTORY_AUDIT_MODELS) == ChainVerification(
            True, None, 3
        )
        lines: list[str] = [line async for line in export_chain(session, INVENTORY_AUDIT_MODELS)]
    assert len(lines) == 4
    assert '"head_seq":3' in lines[-1]
    assert_no_leaks("\n".join(lines), "inventory audit export")
    assert sample.value not in "\n".join(lines)
    async with harness.sessions.begin() as session:
        await session.execute(
            text("UPDATE inventory_audit_records SET summary = 'tampered' WHERE seq = 2")
        )
    async with harness.sessions() as session:
        verification: ChainVerification = await verify_chain(session, INVENTORY_AUDIT_MODELS)
    assert verification == ChainVerification(False, 2, 2)
    async with harness.sessions() as session:
        row: InventoryAuditRecordRow | None = await session.get(InventoryAuditRecordRow, 1)
    assert row is not None
    assert_no_leaks(row.summary + row.details_json, "inventory audit row")
    await harness.close()


@pytest.mark.asyncio
async def test_oidc_authentication_parity_and_missing_fingerprinter_fails_closed() -> None:
    @dataclass(frozen=True)
    class Verifier:
        async def verify(self, token: str) -> VerifiedToken:
            return VerifiedToken("https://issuer.test", "subject-1", "{}")

        async def metadata(self) -> ProviderMetadata:
            raise AssertionError("metadata is not used by authentication")

    @dataclass(frozen=True)
    class Identities(MemberProvisioner):
        async def member_for_identity(
            self, session: AsyncSession, issuer: str, subject: str
        ) -> MemberAuth:
            return MemberAuth(MEMBER_ID, "alice", "Alice Example", Role.ADMIN, True)

        async def sign_in(self, session: AsyncSession, request: SignInRequest) -> SignInResult:
            raise AssertionError("sign-in is not used by authentication")

    fingerprinter: Fingerprinter = Fingerprinter(b"f" * 32, "fixture")
    owner_ref: str = fingerprinter.owner_ref(
        OwnerIdentity(IdentityKind.OIDC_SUBJECT, "https://issuer.test\0subject-1")
    )
    identity_entity: DirectoryEntity = DirectoryEntity(
        EntityKind.IDENTITY,
        "00000000-0000-0000-0000-000000000004",
        2,
        True,
        IdentityEntity(
            "00000000-0000-0000-0000-000000000004",
            MEMBER_ID,
            IdentityKind.OIDC_SUBJECT.value,
            owner_ref.partition(":")[2],
        ),
    )
    entities: tuple[DirectoryEntity, ...] = tuple(
        identity_entity if entity.kind is EntityKind.IDENTITY else entity
        for entity in directory_entities()
    )
    harness: SqliteHarness = await make_sqlite_harness()
    replica: DirectoryReplica = DirectoryReplica(InventoryDirectoryEntryRow)
    async with harness.sessions.begin() as session:
        for entity in entities:
            await replica.apply(session, entity)
    token: str = "header.payload.signature"
    request: Request = Request(
        {"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]}
    )
    audit: AuditLogService = AuditLogService(
        FakeClock(datetime(2026, 10, 16, tzinfo=UTC)), Redactor(RuleDetector())
    )
    context: ApiContext = ApiContext(
        harness.sessions,
        FakeClock(datetime(2026, 10, 16, tzinfo=UTC)),
        audit,
        Verifier(),
        Identities(),
    )
    main_principal: Principal = await authenticate(context, request)
    replica_principal: Principal = await ReplicaAuthenticator(
        harness.sessions, replica, Verifier(), fingerprinter
    )(request)
    assert replica_principal == main_principal
    with pytest.raises(TelescreenError) as error:
        await ReplicaAuthenticator(harness.sessions, replica, Verifier(), None)(request)
    assert error.value.code is ErrorCode.UNAUTHORIZED
    assert error.value.detail == "authentication required"
    await harness.close()
