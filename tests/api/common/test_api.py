"""Contract tests for shared API handling and directory behavior."""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, Request, Response
from httpx import ASGITransport, AsyncClient, Headers
from httpx import Response as HttpxResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.common import (
    ApiContext,
    decode_cursor,
    encode_cursor,
    install_error_handlers,
    read_limited_body,
)
from telescreen.api.common.auth import hash_token
from telescreen.api.routers.directory import DirectoryRouterDeps, build_directory_router
from telescreen.api.routers.health import build_health_router
from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.json import dump_json
from telescreen.core.vocabulary import ApiScope, ErrorCode
from telescreen.db.models import ApiTokenRow
from telescreen.directory import (
    CreatedToken,
    DirectoryRecipientResolver,
    DirectoryService,
    TokenService,
)
from telescreen.ports.audit import AuditEntry
from telescreen.ports.directory import AssigneeIds, ResolvedRecipients
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import Sample, assert_no_leaks, github_token


@dataclass
class RecordingAudit:
    """In-memory audit port fake for directory transactions."""

    entries: list[AuditEntry]

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Record one audit event without writing a separate audit table row."""
        del session
        self.entries.append(entry)
        return len(self.entries)


@pytest.mark.asyncio
async def test_api_auth_no_echo_directory_and_helpers(caplog: pytest.LogCaptureFixture) -> None:
    """Exercise the p10 acceptance contract through an in-process ASGI app."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 8, tzinfo=UTC))
    audit: RecordingAudit = RecordingAudit([])
    ctx: ApiContext = ApiContext(harness.sessions, clock, audit)
    directory: DirectoryService = DirectoryService(audit, clock)
    tokens: TokenService = TokenService(audit, clock)
    async with harness.sessions.begin() as session:
        read_token: CreatedToken = await tokens.create(
            session, "read", frozenset((ApiScope.READ,)), None
        )
        admin_token: CreatedToken = await tokens.create(
            session, "admin", frozenset((ApiScope.ADMIN,)), None
        )
        revoked_token: CreatedToken = await tokens.create(
            session, "revoked", frozenset((ApiScope.READ,)), None
        )
        await tokens.revoke(session, revoked_token.token_id)

    app: FastAPI = FastAPI()
    install_error_handlers(app)
    app.include_router(build_health_router())
    app.include_router(build_directory_router(DirectoryRouterDeps(ctx, directory)))

    @app.get("/explode")
    async def explode() -> Response:
        """Raise an exception containing a synthetic secret for the no-echo check."""
        raise RuntimeError(github_token().value)

    @app.post("/limited")
    async def limited(request: Request) -> Response:
        """Read a request using the configured small body limit."""
        body: bytes = await read_limited_body(request, 3)
        return Response(content=body)

    transport: ASGITransport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        unauthorized: HttpxResponse = await client.get("/api/members")
        assert unauthorized.status_code == 401
        assert unauthorized.json()["error"]["code"] == ErrorCode.UNAUTHORIZED.value

        read_headers: Headers = Headers({"Authorization": f"Bearer {read_token.token}"})
        forbidden: HttpxResponse = await client.post(
            "/api/teams", headers=read_headers, json={"slug": "ops", "name": "Ops"}
        )
        assert forbidden.status_code == 403

        revoked: HttpxResponse = await client.get(
            "/api/members", headers={"Authorization": f"Bearer {revoked_token.token}"}
        )
        assert revoked.status_code == 401

        admin_headers: Headers = Headers({"Authorization": f"Bearer {admin_token.token}"})
        created_member: HttpxResponse = await client.post(
            "/api/members",
            headers=admin_headers,
            json={
                "handle": "alice",
                "display_name": "Alice Example",
                "email": "alice@example.invalid",
                "slack_user_id": "U123",
            },
        )
        assert created_member.status_code == 201
        member_id: str = created_member.json()["id"]
        created_second_member: HttpxResponse = await client.post(
            "/api/members",
            headers=admin_headers,
            json={"handle": "bob", "display_name": "Bob Example"},
        )
        assert created_second_member.status_code == 201
        created_team: HttpxResponse = await client.post(
            "/api/teams",
            headers=admin_headers,
            json={
                "slug": "responders",
                "name": "Responders",
                "email": "team@example.invalid",
                "slack_channel": "C123",
            },
        )
        assert created_team.status_code == 201
        await client.put(
            "/api/teams/responders/members", headers=admin_headers, json={"handles": ["alice"]}
        )
        listed: HttpxResponse = await client.get("/api/members?limit=1", headers=read_headers)
        assert listed.status_code == 200
        assert listed.json()["items"][0]["handle"] == "alice"
        next_cursor: str = listed.json()["next_cursor"]
        next_page: HttpxResponse = await client.get(
            f"/api/members?limit=1&cursor={next_cursor}", headers=read_headers
        )
        assert next_page.json()["items"][0]["handle"] == "bob"
        assert next_page.json()["next_cursor"] is None

        sample: Sample = github_token()
        invalid: HttpxResponse = await client.post(
            "/api/members",
            headers=admin_headers,
            json={"handle": [sample.value], "display_name": "bad"},
        )
        assert invalid.status_code == 422
        assert_no_leaks(invalid.text, "validation response")
        assert invalid.json()["error"]["errors"]
        assert all(set(error) == {"loc", "type"} for error in invalid.json()["error"]["errors"])

        exploded: HttpxResponse = await client.get("/explode")
        assert exploded.status_code == 500
        assert exploded.json()["error"]["error_id"]
        assert_no_leaks(exploded.text, "unexpected error response")
        assert_no_leaks(caplog.text, "API exception logs")

        too_large: HttpxResponse = await client.post("/limited", content=b"1234")
        assert too_large.status_code == 413
        assert too_large.json()["error"]["code"] == ErrorCode.PAYLOAD_TOO_LARGE.value
        assert (await client.get("/api/health")).json() == {"status": "ok"}

    sample: Sample = github_token()
    assert hash_token(admin_token.token) != admin_token.token
    assert admin_token.token not in repr(admin_token)
    async with harness.sessions() as session:
        row: ApiTokenRow | None = await session.scalar(
            select(ApiTokenRow).where(ApiTokenRow.id == admin_token.token_id)
        )
        assert row is not None
        assert row.token_hash == hash_token(admin_token.token)
        assert admin_token.token not in row.token_hash
    assert all(admin_token.token not in dump_json(entry.details) for entry in audit.entries)
    assert_no_leaks(repr(admin_token), "created token repr")
    assert sample.value not in invalid.text

    resolver: DirectoryRecipientResolver = DirectoryRecipientResolver()
    async with harness.sessions.begin() as session:
        resolved_member: ResolvedRecipients = await resolver.resolve(session, "member:alice")
        resolved_team: ResolvedRecipients = await resolver.resolve(session, "team:responders")
        resolved_email: ResolvedRecipients = await resolver.resolve(
            session, "email:direct@example.invalid"
        )
        resolved_slack: ResolvedRecipients = await resolver.resolve(session, "slack:C999")
        assert resolved_member.emails == ("alice@example.invalid",)
        assert resolved_member.slack_user_ids == ("U123",)
        assert resolved_team.emails == ("alice@example.invalid", "team@example.invalid")
        assert resolved_team.slack_channels == ("C123",)
        assert resolved_email.emails == ("direct@example.invalid",)
        assert resolved_slack.slack_channels == ("C999",)
        assert await resolver.is_member_of(session, member_id, "team:responders")
        assert not await resolver.is_member_of(session, member_id, "team:missing")
        with pytest.raises(TelescreenError) as unknown:
            await resolver.resolve(session, "team:missing")
        assert unknown.value.code is ErrorCode.RECIPIENT_UNKNOWN
        member_assignment: AssigneeIds = await resolver.assignee_of(session, "member:alice")
        assert member_assignment.member_id == member_id
        assert member_assignment.team_id is None
        assert (await resolver.assignee_of(session, "team:responders")).team_id is not None

    assert decode_cursor(encode_cursor(("after", "42"))) == ("after", "42")
    with pytest.raises(TelescreenError) as invalid_cursor:
        decode_cursor("not+a+cursor")
    assert invalid_cursor.value.code is ErrorCode.VALIDATION_ERROR
    await harness.close()
