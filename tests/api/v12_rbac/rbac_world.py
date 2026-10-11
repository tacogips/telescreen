"""Shared RBAC test world: container, fake OIDC ports, seeded members and tokens."""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.common import (
    ApiContext,
    PermissionDependency,
    Principal,
    install_error_handlers,
    require_permission,
)
from telescreen.api.routers.ingest import IngestRouterDeps, build_ingest_router
from telescreen.api.routers.issues import IssuesRouterDeps, build_issues_router
from telescreen.core.config import TelescreenSettings
from telescreen.core.errors import TelescreenError
from telescreen.core.permissions import Permission
from telescreen.core.secrets import EnvSource
from telescreen.core.vocabulary import ApiScope, ErrorCode, Role
from telescreen.db.models import MemberRow
from telescreen.directory.tokens import CreatedToken, TokenService
from telescreen.ports.collectors import CollectorConfig
from telescreen.ports.identity import (
    MemberAuth,
    ProviderMetadata,
    SignInRequest,
    SignInResult,
    VerifiedToken,
)
from telescreen.wiring import Container, build_container, close_container
from tests.support.db import SqliteHarness, make_sqlite_harness

ISSUER: str = "https://idp.example.invalid/realms/telescreen"


@dataclass(frozen=True)
class TestEnv(EnvSource):
    """Supply only the test fingerprint secret to the composition root."""

    secret: str

    def get(self, key: str, /) -> str | None:
        """Resolve the configured test secret by its name."""
        return self.secret if key == "TELESCREEN_TEST_KEY" else None


@dataclass(frozen=True)
class TokenSubject:
    """One accepted fake access token and the subject it verifies to."""

    token: str
    subject: str


@dataclass(frozen=True)
class FakeVerifier:
    """BearerVerifier fake that accepts only configured JWT-shaped tokens."""

    accepted: tuple[TokenSubject, ...]
    unavailable: bool = False

    async def verify(self, token: str) -> VerifiedToken:
        """Return the configured identity or raise the port's static errors."""
        if self.unavailable:
            raise TelescreenError(ErrorCode.IDP_UNAVAILABLE, "identity provider unavailable")
        for item in self.accepted:
            if item.token == token:
                return VerifiedToken(ISSUER, item.subject, "{}")
        raise TelescreenError(ErrorCode.INVALID_TOKEN, "invalid token")

    async def metadata(self) -> ProviderMetadata:
        """Return static provider endpoints."""
        return ProviderMetadata(f"{ISSUER}/auth", f"{ISSUER}/token", None)


@dataclass(frozen=True)
class SubjectMember:
    """One linked (issuer, subject) identity and its member state."""

    subject: str
    member: MemberAuth


@dataclass(frozen=True)
class FakeProvisioner:
    """MemberProvisioner fake resolving subjects from a fixed table."""

    links: tuple[SubjectMember, ...]

    async def member_for_identity(
        self, session: AsyncSession, issuer: str, subject: str
    ) -> MemberAuth | None:
        """Return the linked member for the subject, when any."""
        del session
        if issuer != ISSUER:
            return None
        for link in self.links:
            if link.subject == subject:
                return link.member
        return None

    async def sign_in(self, session: AsyncSession, request: SignInRequest) -> SignInResult:
        """Sign-in is out of scope for request authentication tests."""
        del session, request
        raise TelescreenError(ErrorCode.INTERNAL_ERROR, "sign-in is not supported")


@dataclass(frozen=True)
class NoConfigs:
    """CollectorConfigReader fake with no configured sources."""

    async def get(self, session: AsyncSession, collector_config_id: str) -> CollectorConfig | None:
        """Return no collector config."""
        del session, collector_config_id
        return None

    async def get_by_name(self, session: AsyncSession, name: str) -> CollectorConfig | None:
        """Return no named collector config."""
        del session, name
        return None


@dataclass
class RbacWorld:
    """A container-backed database plus apps built with a chosen ApiContext."""

    database: SqliteHarness
    container: Container
    outbound: httpx.AsyncClient

    def context(
        self, verifier: FakeVerifier | None, identities: FakeProvisioner | None
    ) -> ApiContext:
        """Return an ApiContext sharing the container database, clock and audit."""
        return ApiContext(
            self.database.sessions, self.container.clock, self.container.audit, verifier, identities
        )

    def probe_app(self, ctx: ApiContext) -> FastAPI:
        """Build an app with the issues and ingest routers plus a principal probe."""
        app: FastAPI = FastAPI()
        install_error_handlers(app)
        app.include_router(
            build_issues_router(
                IssuesRouterDeps(ctx, self.container.issue_service, self.container.indexer)
            )
        )
        app.include_router(
            build_ingest_router(IngestRouterDeps(ctx, self.container.ingest, NoConfigs()))
        )
        dependency: PermissionDependency = require_permission(ctx, Permission.AUTH_SELF)

        @app.get("/probe")
        async def probe(principal: Annotated[Principal, Depends(dependency)]) -> JSONResponse:
            """Return the authenticated principal's safe fields."""
            return principal_json(principal)

        return app

    async def member(self, member_id: str, role: Role | None, active: bool = True) -> None:
        """Insert a member with a role and activity state."""
        now: datetime = self.container.clock.now()
        async with self.database.sessions.begin() as session:
            session.add(
                MemberRow(
                    id=member_id,
                    handle=member_id,
                    display_name=member_id,
                    email=None,
                    slack_user_id=None,
                    active=active,
                    created_at=now,
                    updated_at=now,
                    role=None if role is None else role.value,
                )
            )

    async def token(
        self, label: str, scopes: frozenset[ApiScope], member_id: str | None
    ) -> CreatedToken:
        """Create a real hashed API token through the directory token service."""
        async with self.database.sessions.begin() as session:
            return await TokenService(self.container.audit, self.container.clock).create(
                session, label, scopes, member_id
            )

    async def revoke(self, token_id: str) -> None:
        """Revoke a token through the directory token service."""
        async with self.database.sessions.begin() as session:
            await TokenService(self.container.audit, self.container.clock).revoke(session, token_id)

    async def close(self) -> None:
        """Close container and database resources."""
        await close_container(self.container)
        await self.outbound.aclose()
        await self.database.close()


def client_for(app: FastAPI) -> AsyncClient:
    """Return an in-process client that renders application errors."""
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    )


def bearer(token: str) -> httpx.Headers:
    """Return an Authorization header for a bearer value."""
    return httpx.Headers({"Authorization": f"Bearer {token}"})


async def make_world() -> RbacWorld:
    """Build a container over an in-memory SQLite database."""
    database: SqliteHarness = await make_sqlite_harness()
    settings: TelescreenSettings = TelescreenSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        fingerprint_key_ref="env:TELESCREEN_TEST_KEY",
    )
    outbound: httpx.AsyncClient = httpx.AsyncClient()
    container: Container = build_container(settings, TestEnv("t" * 32), outbound, database.sessions)
    return RbacWorld(database, container, outbound)


def principal_json(principal: Principal) -> JSONResponse:
    """Render the safe principal fields used by the probe route."""
    return JSONResponse(
        content={
            "kind": principal.kind.value,
            "token_id": principal.token_id,
            "member_id": principal.member_id,
            "role": None if principal.role is None else principal.role.value,
            "actor": principal.actor_ref(),
        }
    )
