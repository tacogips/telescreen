"""Request authentication and permission enforcement for API tokens and OIDC tokens."""

import pytest
from fastapi import FastAPI
from httpx import Headers
from httpx import Response as HttpxResponse

from telescreen.api.app import create_app
from telescreen.api.common import ApiContext, Principal, http_status_for
from telescreen.core.permissions import Permission, scope_permissions
from telescreen.core.vocabulary import (
    ApiScope,
    ErrorCode,
    HumanTaskKind,
    PrincipalKind,
    Role,
)
from telescreen.db.models import HumanTaskRow
from telescreen.directory.tokens import CreatedToken
from telescreen.ports.identity import MemberAuth
from tests.api.v12_rbac.rbac_world import (
    FakeProvisioner,
    FakeVerifier,
    RbacWorld,
    SubjectMember,
    TokenSubject,
    bearer,
    client_for,
    make_world,
)

READ: frozenset[ApiScope] = frozenset((ApiScope.READ,))
READ_WRITE: frozenset[ApiScope] = frozenset((ApiScope.READ, ApiScope.WRITE))
ADMIN: frozenset[ApiScope] = frozenset((ApiScope.ADMIN,))
INGEST: frozenset[ApiScope] = frozenset((ApiScope.INGEST,))
MISSING_ISSUE: str = "/api/issues/00000000-0000-0000-0000-000000000000"


def _error_code(response: HttpxResponse) -> str:
    """Return the stable error code of an application error response."""
    return str(response.json()["error"]["code"])


def test_principal_allows_and_actor_ref() -> None:
    """Principals check permissions by membership and keep v1 actor references."""
    token_principal: Principal = Principal(
        PrincipalKind.TOKEN, "token-1", None, None, scope_permissions(READ)
    )
    assert token_principal.allows(Permission.ISSUES_READ)
    assert not token_principal.allows(Permission.ISSUES_WRITE)
    assert token_principal.actor_ref() == "token:token-1"
    oidc_principal: Principal = Principal(
        PrincipalKind.OIDC, None, "member-1", Role.VIEWER, frozenset()
    )
    assert oidc_principal.actor_ref() == "member:member-1"


def test_http_status_for_v12_codes() -> None:
    """The v1.2 error codes map to their documented HTTP statuses."""
    assert http_status_for(ErrorCode.INVALID_TOKEN) == 401
    assert http_status_for(ErrorCode.NO_ROLE) == 403
    assert http_status_for(ErrorCode.NOT_PROVISIONED) == 403
    assert http_status_for(ErrorCode.IDENTITY_CONFLICT) == 409
    assert http_status_for(ErrorCode.LAST_ADMIN) == 409
    assert http_status_for(ErrorCode.UNKNOWN_TEMPLATE) == 422
    assert http_status_for(ErrorCode.UNKNOWN_VARIABLE) == 422
    assert http_status_for(ErrorCode.TEMPLATE_EVENT_MISMATCH) == 422
    assert http_status_for(ErrorCode.IDP_UNAVAILABLE) == 503


@pytest.mark.asyncio
async def test_missing_malformed_and_revoked_credentials_are_unauthorized() -> None:
    """No header, a non-Bearer header, an empty value and a revoked token give 401."""
    world: RbacWorld = await make_world()
    try:
        revoked: CreatedToken = await world.token("revoked", READ, None)
        await world.revoke(revoked.token_id)
        app: FastAPI = world.probe_app(world.context(None, None))
        async with client_for(app) as client:
            missing: HttpxResponse = await client.get("/api/issues")
            basic: HttpxResponse = await client.get(
                "/api/issues", headers=Headers({"Authorization": "Basic x"})
            )
            empty: HttpxResponse = await client.get(
                "/api/issues", headers=Headers({"Authorization": "Bearer "})
            )
            revoked_response: HttpxResponse = await client.get(
                "/api/issues", headers=bearer(revoked.token)
            )
        for response in (missing, basic, empty, revoked_response):
            assert response.status_code == 401
            assert _error_code(response) == ErrorCode.UNAUTHORIZED.value
        assert revoked.token not in revoked_response.text
    finally:
        await world.close()


@pytest.mark.asyncio
async def test_unbound_tokens_keep_v1_scope_behavior() -> None:
    """Unbound read, write and admin tokens pass exactly the v1 scope checks."""
    world: RbacWorld = await make_world()
    try:
        read: CreatedToken = await world.token("read", READ, None)
        write: CreatedToken = await world.token("write", READ_WRITE, None)
        admin: CreatedToken = await world.token("admin", ADMIN, None)
        app: FastAPI = world.probe_app(world.context(None, None))
        async with client_for(app) as client:
            assert (await client.get("/api/issues", headers=bearer(read.token))).status_code == 200
            read_patch: HttpxResponse = await client.patch(
                MISSING_ISSUE, headers=bearer(read.token), json={}
            )
            assert read_patch.status_code == 403
            assert read_patch.json()["error"]["detail"] == "permission is not granted"
            write_patch: HttpxResponse = await client.patch(
                MISSING_ISSUE, headers=bearer(write.token), json={}
            )
            assert write_patch.status_code == 404
            admin_patch: HttpxResponse = await client.patch(
                MISSING_ISSUE, headers=bearer(admin.token), json={}
            )
            assert admin_patch.status_code == 404
            read_ingest: HttpxResponse = await client.get(
                "/api/ingest/v1/sources/missing/config", headers=bearer(read.token)
            )
            assert read_ingest.status_code == 403
            admin_ingest: HttpxResponse = await client.get(
                "/api/ingest/v1/sources/missing/config", headers=bearer(admin.token)
            )
            assert admin_ingest.status_code == 404
            probe: HttpxResponse = await client.get("/probe", headers=bearer(read.token))
        assert probe.json() == {
            "kind": "token",
            "token_id": read.token_id,
            "member_id": None,
            "role": None,
            "actor": f"token:{read.token_id}",
        }
    finally:
        await world.close()


@pytest.mark.asyncio
async def test_bound_tokens_intersect_scopes_with_member_role() -> None:
    """A bound write token is limited by its member's current role and activity."""
    world: RbacWorld = await make_world()
    try:
        await world.member("member-viewer", Role.VIEWER)
        await world.member("member-responder", Role.RESPONDER)
        await world.member("member-inactive", Role.RESPONDER, active=False)
        viewer: CreatedToken = await world.token("viewer", READ_WRITE, "member-viewer")
        responder: CreatedToken = await world.token("responder", READ_WRITE, "member-responder")
        inactive: CreatedToken = await world.token("inactive", READ_WRITE, "member-inactive")
        app: FastAPI = world.probe_app(world.context(None, None))
        async with client_for(app) as client:
            assert (
                await client.get("/api/issues", headers=bearer(viewer.token))
            ).status_code == 200
            viewer_patch: HttpxResponse = await client.patch(
                MISSING_ISSUE, headers=bearer(viewer.token), json={}
            )
            assert viewer_patch.status_code == 403
            responder_patch: HttpxResponse = await client.patch(
                MISSING_ISSUE, headers=bearer(responder.token), json={}
            )
            assert responder_patch.status_code == 404
            inactive_read: HttpxResponse = await client.get(
                "/api/issues", headers=bearer(inactive.token)
            )
            assert inactive_read.status_code == 403
            probe: HttpxResponse = await client.get("/probe", headers=bearer(responder.token))
        assert probe.json()["role"] == "responder"
        assert probe.json()["actor"] == "member:member-responder"
    finally:
        await world.close()


@pytest.mark.asyncio
async def test_d11_role_null_and_missing_member_keep_only_machine_permissions() -> None:
    """Role NULL or a missing member removes human permissions but keeps ingest.push."""
    world: RbacWorld = await make_world()
    try:
        await world.member("member-unassigned", None)
        human: CreatedToken = await world.token("human", READ_WRITE, "member-unassigned")
        ingest: CreatedToken = await world.token("ingest", INGEST, "member-unassigned")
        orphan: CreatedToken = await world.token("orphan", READ_WRITE, "member-missing")
        app: FastAPI = world.probe_app(world.context(None, None))
        async with client_for(app) as client:
            human_read: HttpxResponse = await client.get("/api/issues", headers=bearer(human.token))
            assert human_read.status_code == 403
            scan: HttpxResponse = await client.post(
                "/api/ingest/v1/scan", headers=bearer(ingest.token), content=b""
            )
            assert scan.status_code not in (401, 403)
            config: HttpxResponse = await client.get(
                "/api/ingest/v1/sources/missing/config", headers=bearer(ingest.token)
            )
            assert config.status_code == 404
            orphan_read: HttpxResponse = await client.get(
                "/api/issues", headers=bearer(orphan.token)
            )
            assert orphan_read.status_code == 403
    finally:
        await world.close()


@pytest.mark.asyncio
async def test_oidc_access_tokens_use_role_permissions() -> None:
    """JWT-shaped bearer values go through the verifier and the linked member's role."""
    world: RbacWorld = await make_world()
    try:
        await world.member("member-oidc-viewer", Role.VIEWER)
        verifier: FakeVerifier = FakeVerifier(
            (
                TokenSubject("a.b.c", "subject-viewer"),
                TokenSubject("a.b.unknown", "subject-unknown"),
                TokenSubject("a.b.norole", "subject-norole"),
                TokenSubject("a.b.inactive", "subject-inactive"),
            )
        )
        identities: FakeProvisioner = FakeProvisioner(
            (
                SubjectMember(
                    "subject-viewer",
                    MemberAuth("member-oidc-viewer", "viewer", "Viewer", Role.VIEWER, True),
                ),
                SubjectMember(
                    "subject-norole", MemberAuth("member-norole", "norole", "No Role", None, True)
                ),
                SubjectMember(
                    "subject-inactive",
                    MemberAuth("member-inactive", "inactive", "Inactive", Role.ADMIN, False),
                ),
            )
        )
        oidc_app: FastAPI = world.probe_app(world.context(verifier, identities))
        async with client_for(oidc_app) as client:
            viewer_read: HttpxResponse = await client.get("/api/issues", headers=bearer("a.b.c"))
            assert viewer_read.status_code == 200
            viewer_patch: HttpxResponse = await client.patch(
                MISSING_ISSUE, headers=bearer("a.b.c"), json={}
            )
            assert viewer_patch.status_code == 403
            probe: HttpxResponse = await client.get("/probe", headers=bearer("a.b.c"))
            assert probe.json() == {
                "kind": "oidc",
                "token_id": None,
                "member_id": "member-oidc-viewer",
                "role": "viewer",
                "actor": "member:member-oidc-viewer",
            }
            unknown: HttpxResponse = await client.get("/api/issues", headers=bearer("a.b.unknown"))
            assert unknown.status_code == 401
            assert unknown.json()["error"]["detail"] == "sign-in required"
            no_role: HttpxResponse = await client.get("/api/issues", headers=bearer("a.b.norole"))
            assert no_role.status_code == 403
            inactive: HttpxResponse = await client.get(
                "/api/issues", headers=bearer("a.b.inactive")
            )
            assert inactive.status_code == 403
            invalid: HttpxResponse = await client.get("/api/issues", headers=bearer("x.y.z"))
            assert invalid.status_code == 401
            assert _error_code(invalid) == ErrorCode.INVALID_TOKEN.value
            assert "x.y.z" not in invalid.text
        no_verifier_app: FastAPI = world.probe_app(world.context(None, None))
        async with client_for(no_verifier_app) as client:
            disabled: HttpxResponse = await client.get("/api/issues", headers=bearer("a.b.c"))
            assert disabled.status_code == 401
            assert _error_code(disabled) == ErrorCode.UNAUTHORIZED.value
        down_ctx: ApiContext = world.context(FakeVerifier((), unavailable=True), identities)
        async with client_for(world.probe_app(down_ctx)) as client:
            down: HttpxResponse = await client.get("/api/issues", headers=bearer("a.b.c"))
            assert down.status_code == 503
            assert _error_code(down) == ErrorCode.IDP_UNAVAILABLE.value
    finally:
        await world.close()


@pytest.mark.asyncio
async def test_in_handler_manage_any_checks_preserve_v1_admin_behavior() -> None:
    """Only manage_any permissions list all tasks or complete another member's task."""
    world: RbacWorld = await make_world()
    try:
        await world.member("member-alice", Role.RESPONDER)
        async with world.database.sessions.begin() as session:
            session.add(
                HumanTaskRow(
                    id="task-alice",
                    run_id="run-missing",
                    step_run_id="step-missing",
                    kind=HumanTaskKind.APPROVAL.value,
                    title="Approve",
                    assignee_ref="member:member-alice",
                    status="open",
                )
            )
        write: CreatedToken = await world.token("write", READ_WRITE, None)
        admin: CreatedToken = await world.token("admin", ADMIN, None)
        app: FastAPI = create_app(world.container)
        async with client_for(app) as client:
            write_all: HttpxResponse = await client.get(
                "/api/human-tasks?assignee=all", headers=bearer(write.token)
            )
            assert write_all.status_code == 403
            admin_all: HttpxResponse = await client.get(
                "/api/human-tasks?assignee=all", headers=bearer(admin.token)
            )
            assert admin_all.status_code == 200
            assert [item["id"] for item in admin_all.json()["items"]] == ["task-alice"]
            write_complete: HttpxResponse = await client.post(
                "/api/human-tasks/task-alice/complete",
                headers=bearer(write.token),
                json={"outcome": "approved"},
            )
            assert write_complete.status_code == 403
            assert write_complete.json()["error"]["detail"] == (
                "task is assigned to another recipient"
            )
            # The admin passes the ownership gate and reaches the workflow context lookup.
            admin_complete: HttpxResponse = await client.post(
                "/api/human-tasks/task-alice/complete",
                headers=bearer(admin.token),
                json={"outcome": "approved"},
            )
            assert admin_complete.status_code == 404
            assert admin_complete.json()["error"]["detail"] == "workflow task context not found"
    finally:
        await world.close()
