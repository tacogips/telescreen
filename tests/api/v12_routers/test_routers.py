"""Request-level coverage for the v1.2 API routers."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from httpx import Response as HttpxResponse
from sqlalchemy import select

from telescreen.api.common import (
    ExternalAuthDeclaration,
    PermissionDependency,
    install_error_handlers,
)
from telescreen.api.common.context import ApiContext
from telescreen.api.routers.auth import AuthRouterDeps, build_auth_router
from telescreen.api.routers.notification_templates import (
    NotificationTemplatesRouterDeps,
    build_notification_templates_router,
)
from telescreen.api.routers.users import UsersRouterDeps, build_users_router
from telescreen.core.clock import Clock
from telescreen.core.errors import TelescreenError
from telescreen.core.oidc_settings import OidcSettings
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import ApiScope, DlpPolicy, ErrorCode, Role
from telescreen.db.models import AuditRecordRow, MemberRow
from telescreen.detection import RuleDetector
from telescreen.directory.provisioning import DirectoryProvisioner
from telescreen.directory.roles import RoleService
from telescreen.directory.tokens import CreatedToken, TokenService
from telescreen.identity.signin import SignInService
from telescreen.masking.redact import Redactor
from telescreen.notifications.dlp import DlpGate
from telescreen.notifications.templates.repository import TemplateRepository
from telescreen.notifications.templates.service import TemplateService
from telescreen.ports.identity import ProviderMetadata, VerifiedToken
from tests.api.v12_rbac.rbac_world import RbacWorld, bearer, client_for, make_world
from tests.identity.oidc_fixtures import EMAIL, ISSUER, SUBJECT, base_settings, claims_document
from tests.support.db import SqliteHarness

NOW: datetime = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
OIDC_ACCESS: str = "synthetic-oidc-access-token"


@dataclass(frozen=True)
class StaticVerifier:
    """Provide fixed verified claims and public metadata for router tests."""

    claims_json: str
    unavailable: bool = False

    async def verify(self, token: str) -> VerifiedToken:
        """Return the fixed test identity without retaining the presented token."""
        if token != OIDC_ACCESS:
            raise TelescreenError(ErrorCode.INVALID_TOKEN, "invalid token")
        return VerifiedToken(ISSUER, SUBJECT, self.claims_json)

    async def metadata(self) -> ProviderMetadata:
        """Return fixed public endpoints or simulate discovery being unavailable."""
        if self.unavailable:
            raise TelescreenError(ErrorCode.IDP_UNAVAILABLE, "identity provider unavailable")
        return ProviderMetadata(f"{ISSUER}/auth", f"{ISSUER}/token", f"{ISSUER}/logout")


async def _world() -> RbacWorld:
    """Create the shared SQLite-backed API world."""
    return await make_world()


def _routers(
    world: RbacWorld,
    settings: OidcSettings | None = None,
    verifier: StaticVerifier | None = None,
    sign_in: SignInService | None = None,
) -> tuple[APIRouter, ...]:
    """Build the three p10 routers over shared test services."""
    context: ApiContext = ApiContext(
        world.database.sessions,
        world.container.clock,
        world.container.audit,
        verifier,
        None,
    )
    redactor: Redactor = Redactor.default()
    templates: TemplateService = TemplateService(
        TemplateRepository(),
        redactor,
        DlpGate(RuleDetector(), redactor, DlpPolicy.MASK),
        world.container.audit,
        world.container.clock,
    )
    template_router: APIRouter = build_notification_templates_router(
        NotificationTemplatesRouterDeps(context, templates)
    )
    users_router: APIRouter = build_users_router(
        UsersRouterDeps(
            context,
            RoleService(world.container.audit, world.container.clock),
            TokenService(world.container.audit, world.container.clock),
            True,
        )
    )
    auth_router: APIRouter = build_auth_router(
        AuthRouterDeps(context, settings, True, verifier, sign_in)
    )
    return (template_router, users_router, auth_router)


def _app(
    world: RbacWorld,
    settings: OidcSettings | None = None,
    verifier: StaticVerifier | None = None,
    sign_in: SignInService | None = None,
) -> FastAPI:
    """Build an app containing only the three p10 routers and common errors."""
    app: FastAPI = FastAPI()
    install_error_handlers(app)
    for router in _routers(world, settings, verifier, sign_in):
        app.include_router(router)
    return app


async def _seed_member(
    database: SqliteHarness,
    clock: Clock,
    member_id: str,
    handle: str,
    role: Role | None,
    email: str | None = None,
) -> None:
    """Insert a member for role and OIDC link tests."""
    async with database.sessions.begin() as session:
        session.add(
            MemberRow(
                id=member_id,
                handle=handle,
                display_name=handle.title(),
                email=email,
                slack_user_id=None,
                active=True,
                created_at=clock.now(),
                updated_at=clock.now(),
                role=role.value if role is not None else None,
            )
        )


@pytest.mark.asyncio
async def test_template_routes_create_activate_preview_and_hide_invalid_text() -> None:
    """Template endpoints expose text only on success and protect writes by permission."""
    world: RbacWorld = await _world()
    try:
        admin: CreatedToken = await world.token(
            "template-admin", frozenset((ApiScope.ADMIN,)), None
        )
        viewer: CreatedToken = await world.token(
            "template-viewer", frozenset((ApiScope.READ,)), None
        )
        app: FastAPI = _app(world)
        async with client_for(app) as client:
            listed: HttpxResponse = await client.get(
                "/api/notification-templates", headers=bearer(viewer.token)
            )
            assert listed.status_code == 200
            forbidden_create: HttpxResponse = await client.post(
                "/api/notification-templates",
                headers=bearer(viewer.token),
                json={
                    "name": "custom.denied",
                    "event_kind": "workflow_notify",
                    "subject": "Alert",
                    "body": "Body",
                },
            )
            assert forbidden_create.status_code == 403
            created: HttpxResponse = await client.post(
                "/api/notification-templates",
                headers=bearer(admin.token),
                json={
                    "name": "custom.workflow",
                    "event_kind": "workflow_notify",
                    "subject": "Alert {{ issue.short_id }}",
                    "body": "{{ issue.title }}",
                },
            )
            assert created.status_code == 201
            assert created.json() == {"name": "custom.workflow", "version": 1}
            valid: HttpxResponse = await client.post(
                "/api/notification-templates/validate",
                headers=bearer(admin.token),
                json={
                    "event_kind": "workflow_notify",
                    "subject": "Alert {{ issue.short_id }}",
                    "body": "{{ issue.title }}",
                },
            )
            assert valid.status_code == 200
            assert valid.json() == {"valid": True, "errors": []}
            invalid_text: str = "contains {{ issue.secret }}"
            invalid: HttpxResponse = await client.post(
                "/api/notification-templates",
                headers=bearer(admin.token),
                json={
                    "name": "custom.invalid",
                    "event_kind": "workflow_notify",
                    "subject": "Alert",
                    "body": invalid_text,
                },
            )
            assert invalid.status_code == 422
            assert invalid.json()["errors"][0] == {
                "field": "body",
                "code": "unknown_variable",
            }
            assert invalid_text not in invalid.text
            async with world.database.sessions() as session:
                rejections: Sequence[AuditRecordRow] = tuple(
                    await session.scalars(
                        select(AuditRecordRow).where(AuditRecordRow.action == "template.rejected")
                    )
                )
            assert len(rejections) == 1
            assert rejections[0].target_id == "custom.invalid"
            assert "unknown_variable" in rejections[0].details_json
            assert invalid_text not in rejections[0].details_json
            preview: HttpxResponse = await client.post(
                "/api/notification-templates/preview",
                headers=bearer(admin.token),
                json={"name": "custom.workflow", "version": 1},
            )
            assert preview.status_code == 200
            assert set(preview.json()) == {"email", "slack"}
            mixed: HttpxResponse = await client.post(
                "/api/notification-templates/preview",
                headers=bearer(admin.token),
                json={
                    "name": "custom.workflow",
                    "version": 1,
                    "event_kind": "workflow_notify",
                    "subject": "x",
                    "body": "y",
                },
            )
            assert mixed.status_code == 422
            activated: HttpxResponse = await client.post(
                "/api/notification-templates/custom.workflow/activate",
                headers=bearer(admin.token),
                json={"version": 1},
            )
            assert activated.status_code == 200
            detail: HttpxResponse = await client.get(
                "/api/notification-templates/custom.workflow", headers=bearer(admin.token)
            )
            assert detail.json()["active_version"] == 1
    finally:
        await world.close()


@pytest.mark.asyncio
async def test_users_roles_tokens_permissions_and_revocation() -> None:
    """Role assignment is audited, last-admin is protected and tokens are one-time values."""
    world: RbacWorld = await _world()
    try:
        await _seed_member(world.database, world.container.clock, "m-admin", "alice", Role.ADMIN)
        await _seed_member(world.database, world.container.clock, "m-bob", "bob", Role.RESPONDER)
        await _seed_member(
            world.database, world.container.clock, "m-charlie", "charlie", Role.RESPONDER
        )
        admin: CreatedToken = await world.token("router-admin", frozenset((ApiScope.ADMIN,)), None)
        app: FastAPI = _app(world)
        async with client_for(app) as client:
            role_list: HttpxResponse = await client.get("/api/roles", headers=bearer(admin.token))
            assert len(role_list.json()["roles"]) == 3
            assert len(role_list.json()["scopes"]) == 4
            changed: HttpxResponse = await client.put(
                "/api/users/bob/role",
                headers=bearer(admin.token),
                json={"role": "viewer"},
            )
            assert changed.status_code == 200
            assert changed.json()["role"] == "viewer"
            async with world.database.sessions() as session:
                role_events: Sequence[AuditRecordRow] = tuple(
                    await session.scalars(
                        select(AuditRecordRow).where(AuditRecordRow.action == "role.changed")
                    )
                )
            assert any(event.target_id == "m-bob" for event in role_events)
            last_admin: HttpxResponse = await client.put(
                "/api/users/alice/role",
                headers=bearer(admin.token),
                json={"role": None},
            )
            assert last_admin.status_code == 409
            assert last_admin.json()["error"]["code"] == "last_admin"
            created: HttpxResponse = await client.post(
                "/api/tokens",
                headers=bearer(admin.token),
                json={"label": "bob-reader", "scopes": ["read"], "member_handle": "bob"},
            )
            assert created.status_code == 201
            token_value: str = created.json()["token"]
            listing: HttpxResponse = await client.get("/api/tokens", headers=bearer(admin.token))
            assert token_value not in listing.text
            assert "token_hash" not in listing.text
            empty_scopes: HttpxResponse = await client.post(
                "/api/tokens",
                headers=bearer(admin.token),
                json={"label": "empty", "scopes": []},
            )
            assert empty_scopes.status_code == 422
            unknown_scope: HttpxResponse = await client.post(
                "/api/tokens",
                headers=bearer(admin.token),
                json={"label": "unknown", "scopes": ["invalid"]},
            )
            assert unknown_scope.status_code == 422
            unknown_member: HttpxResponse = await client.post(
                "/api/tokens",
                headers=bearer(admin.token),
                json={"label": "unbound", "scopes": ["read"], "member_handle": "missing"},
            )
            assert unknown_member.status_code == 404
            me: HttpxResponse = await client.get("/api/auth/me", headers=bearer(token_value))
            assert me.status_code == 200
            assert me.json()["member"]["handle"] == "bob"
            responder_token: HttpxResponse = await client.post(
                "/api/tokens",
                headers=bearer(admin.token),
                json={
                    "label": "charlie-admin-scope",
                    "scopes": ["admin"],
                    "member_handle": "charlie",
                },
            )
            assert responder_token.status_code == 201
            denied: HttpxResponse = await client.get(
                "/api/users", headers=bearer(responder_token.json()["token"])
            )
            assert denied.status_code == 403
            revoke: HttpxResponse = await client.post(
                f"/api/tokens/{created.json()['id']}/revoke", headers=bearer(admin.token)
            )
            assert revoke.status_code == 204
            revoked: HttpxResponse = await client.get("/api/auth/me", headers=bearer(token_value))
            assert revoked.status_code == 401
    finally:
        await world.close()


@pytest.mark.asyncio
async def test_auth_metadata_sign_in_and_safe_session_projection() -> None:
    """OIDC routes return public metadata and safe member data with service error codes intact."""
    world: RbacWorld = await _world()
    try:
        await _seed_member(
            world.database, world.container.clock, "m-alice", "alice", Role.VIEWER, EMAIL
        )
        claims: str = claims_document(NOW)
        verifier: StaticVerifier = StaticVerifier(claims)
        roles: RoleService = RoleService(world.container.audit, world.container.clock)
        provisioner: DirectoryProvisioner = DirectoryProvisioner(
            world.container.fingerprinter, roles, world.container.audit, world.container.clock
        )
        sign_in: SignInService = SignInService(
            world.database.sessions,
            verifier,
            provisioner,
            base_settings(),
            world.container.audit,
            world.container.fingerprinter,
        )
        app: FastAPI = _app(world, base_settings(), verifier, sign_in)
        token_only_app: FastAPI = _app(world)
        async with client_for(token_only_app) as token_only_client:
            token_config: HttpxResponse = await token_only_client.get("/api/auth/config")
            assert token_config.json() == {"oidc": None, "token_login": True}
        async with client_for(app) as client:
            config: HttpxResponse = await client.get("/api/auth/config")
            assert config.json()["oidc"]["issuer"] == ISSUER
            session: HttpxResponse = await client.post(
                "/api/auth/session", headers=bearer(OIDC_ACCESS)
            )
            assert session.status_code == 200
            assert session.json()["member"] == {
                "handle": "alice",
                "display_name": "Alice",
            }
            assert session.json()["role"] == "admin"
            assert "sub" not in session.text and "claims" not in session.text
            assert OIDC_ACCESS not in session.text
            token: CreatedToken = await world.token("me-reader", frozenset((ApiScope.READ,)), None)
            me: HttpxResponse = await client.get("/api/auth/me", headers=bearer(token.token))
            assert me.json()["kind"] == "token"
            assert me.json()["member"] is None
        unavailable: StaticVerifier = StaticVerifier(claims, unavailable=True)
        fallback_app: FastAPI = _app(world, base_settings(), unavailable, None)
        async with client_for(fallback_app) as client:
            fallback: HttpxResponse = await client.get("/api/auth/config")
            assert fallback.status_code == 200
            assert fallback.json() == {"oidc": None, "token_login": True}
        no_role_verifier: StaticVerifier = StaticVerifier("{}")
        no_role_sign_in: SignInService = SignInService(
            world.database.sessions,
            no_role_verifier,
            provisioner,
            base_settings(),
            world.container.audit,
            world.container.fingerprinter,
        )
        no_role_app: FastAPI = _app(world, base_settings(), no_role_verifier, no_role_sign_in)
        async with client_for(no_role_app) as client:
            no_role: HttpxResponse = await client.post(
                "/api/auth/session", headers=bearer(OIDC_ACCESS)
            )
            assert no_role.status_code == 403
            assert no_role.json()["error"]["code"] == "no_role"
    finally:
        await world.close()


@pytest.mark.asyncio
async def test_router_routes_declare_one_permission_or_external_auth() -> None:
    """Every p10 route has exactly one authorization marker."""
    world: RbacWorld = await _world()
    try:
        routes: tuple[APIRoute, ...] = tuple(
            route
            for router in _routers(world)
            for route in router.routes
            if isinstance(route, APIRoute)
        )
        assert len(routes) == 16
        for route in routes:
            markers: list[Permission | str] = []
            for dependency in route.dependant.dependencies:
                if isinstance(dependency.call, PermissionDependency):
                    markers.append(dependency.call.permission)
                elif isinstance(dependency.call, ExternalAuthDeclaration):
                    markers.append(dependency.call.kind.value)
            assert len(markers) == 1, route.path
    finally:
        await world.close()
