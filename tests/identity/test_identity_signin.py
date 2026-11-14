"""Claim mapping and SignInService denial, provisioning and audit behavior."""

import dataclasses
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.audit.service import AuditLogService
from telescreen.core.errors import TelescreenError
from telescreen.core.json import (
    JsonValue,
    json_array,
    json_bool,
    json_int,
    json_object,
    json_str,
    parse_json,
)
from telescreen.core.oidc_settings import OidcSettings, OidcTeamMapping
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import (
    ActorKind,
    ErrorCode,
    IdentityKind,
    OidcProvisioning,
    Role,
    SignInProvisioning,
)
from telescreen.db.models import AuditRecordRow
from telescreen.detection import RuleDetector
from telescreen.identity.claims import ClaimProfile, claim_strings, map_role, profile
from telescreen.identity.signin import SignInService, identity_key_prefix
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry
from telescreen.ports.collectors import OwnerIdentity
from telescreen.ports.identity import MemberAuth, SignInRequest, SignInResult
from tests.identity.oidc_fixtures import (
    EMAIL,
    ISSUER,
    SUBJECT,
    VerifierHarness,
    base_settings,
    make_harness,
)
from tests.support.db import SqliteHarness, make_sqlite_harness


def _roles(*values: str) -> JsonValue:
    """Build a claim set carrying realm roles."""
    return json_object(
        [("realm_access", json_object([("roles", json_array([json_str(v) for v in values]))]))]
    )


def test_role_mapping_takes_highest_mapped_role() -> None:
    settings: OidcSettings = base_settings()

    assert map_role(_roles("telescreen-viewer", "telescreen-admin"), settings) is Role.ADMIN
    assert map_role(_roles("telescreen-responder", "offline_access"), settings) is Role.RESPONDER
    assert map_role(_roles("offline_access"), settings) is None
    assert map_role(json_object([]), settings) is None


def test_default_role_applies_only_without_mapped_role() -> None:
    settings: OidcSettings = dataclasses.replace(base_settings(), default_role=Role.VIEWER)

    assert map_role(_roles("offline_access"), settings) is Role.VIEWER
    assert map_role(_roles("telescreen-admin"), settings) is Role.ADMIN


def test_claim_strings_accepts_string_or_string_array_only() -> None:
    claims: JsonValue = json_object(
        [
            ("groups", json_str("security")),
            ("teams", json_array([json_str("dev"), json_str("ghe-admin")])),
            ("mixed", json_array([json_str("dev"), json_int(1)])),
            ("flag", json_bool(True)),
            ("nested", json_object([("deep", json_object([("roles", json_str("a"))]))])),
        ]
    )

    assert claim_strings(claims, "groups") == ("security",)
    assert claim_strings(claims, "teams") == ("dev", "ghe-admin")
    assert claim_strings(claims, "mixed") == ()
    assert claim_strings(claims, "flag") == ()
    assert claim_strings(claims, "missing") == ()
    assert claim_strings(claims, "nested.deep.roles") == ("a",)
    assert claim_strings(claims, "groups.inner") == ()


def test_profile_requires_boolean_email_verified_and_hides_email() -> None:
    claims: JsonValue = json_object(
        [
            ("email", json_str(EMAIL)),
            ("email_verified", json_str("true")),
            ("preferred_username", json_str("alice")),
            ("name", json_str("")),
        ]
    )

    result: ClaimProfile = profile(claims)

    assert result.email == EMAIL
    assert result.email_verified is False
    assert result.preferred_username == "alice"
    assert result.name is None
    assert EMAIL not in repr(result)


def _fingerprinter() -> Fingerprinter:
    """Create a deterministic test fingerprinter."""
    return Fingerprinter.from_secret(SecretValue("identity-test-key-material-32-bytes"))


@dataclass
class FakeProvisioner:
    """Record sign-in requests and either succeed or raise a configured error."""

    audit: AuditLogService
    error: ErrorCode | None = None
    requests: list[SignInRequest] = field(default_factory=list)

    async def member_for_identity(
        self, session: AsyncSession, issuer: str, subject: str
    ) -> MemberAuth | None:
        """Return no linked member; unused by sign-in orchestration."""
        del session, issuer, subject
        return None

    async def sign_in(self, session: AsyncSession, request: SignInRequest) -> SignInResult:
        """Write inside the caller's transaction, then succeed or raise."""
        self.requests.append(request)
        await self.audit.append(
            session,
            AuditEntry(
                ActorKind.SYSTEM,
                "system:test",
                "test.provision_attempt",
                "member",
                "m-1",
                "Provisioning attempted",
                json_int(1),
            ),
        )
        if self.error is not None:
            raise TelescreenError(self.error, "sign-in denied")
        return SignInResult(
            member=MemberAuth("m-1", "alice", "Alice Example", request.role, True),
            provisioning=SignInProvisioning.LINKED,
            role_changed=True,
        )


@dataclass
class SignInHarness:
    """SignInService over SQLite, the real audit log and an offline IdP."""

    db: SqliteHarness
    verifier: VerifierHarness
    provisioner: FakeProvisioner
    service: SignInService

    async def audit_rows(self) -> list[AuditRecordRow]:
        """Return committed audit rows in sequence order."""
        async with self.db.sessions() as session:
            return list(
                (await session.scalars(select(AuditRecordRow).order_by(AuditRecordRow.seq))).all()
            )


@pytest.fixture
async def signin() -> AsyncIterator[SignInHarness]:
    """Build a sign-in harness and dispose of its database afterwards."""
    db: SqliteHarness = await make_sqlite_harness()
    settings: OidcSettings = dataclasses.replace(
        base_settings(),
        provisioning=OidcProvisioning.CREATE,
        team_map=(OidcTeamMapping("security", "secops"),),
    )
    verifier: VerifierHarness = make_harness(settings)
    audit: AuditLogService = AuditLogService(verifier.clock, Redactor(RuleDetector()))
    provisioner: FakeProvisioner = FakeProvisioner(audit)
    service: SignInService = SignInService(
        db.sessions, verifier.verifier, provisioner, settings, audit, _fingerprinter()
    )
    yield SignInHarness(db, verifier, provisioner, service)
    await db.close()


def _assert_no_identity_values(rows: list[AuditRecordRow], token: str) -> None:
    """Assert audit rows hold no token, subject, email or issuer value."""
    for row in rows:
        stored: str = f"{row.actor_ref} {row.target_id} {row.summary} {row.details_json}"
        assert token not in stored
        assert token.split(".")[1] not in stored
        assert SUBJECT not in stored
        assert EMAIL not in stored
        assert ISSUER not in stored


async def test_successful_sign_in_provisions_and_audits_safe_details(
    signin: SignInHarness,
) -> None:
    token: str = signin.verifier.mint()

    result: SignInResult = await signin.service.sign_in(token)

    assert result.member.member_id == "m-1"
    request: SignInRequest = signin.provisioner.requests[0]
    assert request.issuer == ISSUER
    assert request.subject == SUBJECT
    assert request.email == EMAIL and request.email_verified is True
    assert request.preferred_username == "alice"
    assert request.display_name == "Alice Example"
    assert request.role is Role.ADMIN
    assert request.groups == ("security",)
    assert request.provisioning is OidcProvisioning.CREATE
    assert request.team_map == (OidcTeamMapping("security", "secops"),)
    assert SUBJECT not in repr(request) and EMAIL not in repr(request)
    rows: list[AuditRecordRow] = await signin.audit_rows()
    assert [row.action for row in rows] == ["test.provision_attempt", "auth.sign_in"]
    success: AuditRecordRow = rows[1]
    assert success.actor_kind == ActorKind.MEMBER.value
    assert success.actor_ref == "member:m-1"
    details: JsonValue = parse_json(success.details_json)
    assert details.member_names() == ("method", "provisioning", "role")
    method: JsonValue | None = details.get("method")
    role: JsonValue | None = details.get("role")
    provisioning: JsonValue | None = details.get("provisioning")
    assert method is not None and method.as_str() == "oidc"
    assert role is not None and role.as_str() == "admin"
    assert provisioning is not None and provisioning.as_str() == "linked"
    _assert_no_identity_values(rows, token)


async def test_no_role_denies_before_provisioning_and_audits_key_prefix(
    signin: SignInHarness,
) -> None:
    token: str = signin.verifier.mint(extra=(("realm_access", _roles("offline_access")),))

    with pytest.raises(TelescreenError) as caught:
        await signin.service.sign_in(token)

    assert caught.value.code is ErrorCode.NO_ROLE
    assert signin.provisioner.requests == []
    rows: list[AuditRecordRow] = await signin.audit_rows()
    assert [row.action for row in rows] == ["auth.sign_in_denied"]
    denial: AuditRecordRow = rows[0]
    assert denial.actor_kind == ActorKind.SYSTEM.value
    assert denial.actor_ref == "system:auth"
    assert denial.target_kind == "auth"
    details: JsonValue = parse_json(denial.details_json)
    reason: JsonValue | None = details.get("reason")
    key_prefix: JsonValue | None = details.get("key_prefix")
    assert reason is not None and reason.as_str() == "no_role"
    expected_key: str = (
        _fingerprinter()
        .owner_ref(OwnerIdentity(IdentityKind.OIDC_SUBJECT, f"{ISSUER}\0{SUBJECT}"))
        .partition(":")[2]
    )
    assert key_prefix is not None and key_prefix.as_str() == expected_key[:12]
    assert identity_key_prefix(_fingerprinter(), ISSUER, SUBJECT) == expected_key[:12]
    _assert_no_identity_values(rows, token)


@pytest.mark.parametrize(
    "code",
    [ErrorCode.NOT_PROVISIONED, ErrorCode.IDENTITY_CONFLICT, ErrorCode.FORBIDDEN],
)
async def test_provisioner_denial_rolls_back_and_is_audited_separately(
    signin: SignInHarness, code: ErrorCode
) -> None:
    signin.provisioner.error = code
    token: str = signin.verifier.mint()

    with pytest.raises(TelescreenError) as caught:
        await signin.service.sign_in(token)

    assert caught.value.code is code
    rows: list[AuditRecordRow] = await signin.audit_rows()
    assert [row.action for row in rows] == ["auth.sign_in_denied"]
    reason: JsonValue | None = parse_json(rows[0].details_json).get("reason")
    assert reason is not None and reason.as_str() == code.value
    _assert_no_identity_values(rows, token)


async def test_other_provisioner_errors_are_reraised_without_denial_audit(
    signin: SignInHarness,
) -> None:
    signin.provisioner.error = ErrorCode.VALIDATION_ERROR

    with pytest.raises(TelescreenError) as caught:
        await signin.service.sign_in(signin.verifier.mint())

    assert caught.value.code is ErrorCode.VALIDATION_ERROR
    assert await signin.audit_rows() == []


async def test_invalid_token_opens_no_transaction_and_writes_no_audit(
    signin: SignInHarness,
) -> None:
    token: str = signin.verifier.mint(extra=(("aud", json_str("another-client")),))

    with pytest.raises(TelescreenError) as caught:
        await signin.service.sign_in(token)

    assert caught.value.code is ErrorCode.INVALID_TOKEN
    assert signin.provisioner.requests == []
    assert await signin.audit_rows() == []
