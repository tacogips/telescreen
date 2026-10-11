"""End-to-end OIDC role mapping, permission intersection, and admin recovery."""

import argparse
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from telescreen.cli import _run_async_command
from telescreen.core.json import json_array, json_bool, json_object, json_str
from telescreen.core.vocabulary import ApiScope, Role
from telescreen.db.models import AuditRecordRow, MemberRow
from telescreen.directory.tokens import CreatedToken, TokenService
from tests.e2e.harness import E2EWorld, OutboundRecorder, make_world
from tests.identity.oidc_fixtures import AUDIENCE, ISSUER, claims_document
from tests.support.oidc import TestIdp as LocalIdp
from tests.support.samples import github_token


def _claims(subject: str, username: str, role: str | None, groups: tuple[str, ...]) -> str:
    """Build one Keycloak-shaped token with a chosen role and team group."""
    role_values: tuple[str, ...] = (f"telescreen-{role}",) if role is not None else ()
    return claims_document(
        datetime.now(UTC),
        (
            ("sub", json_str(subject)),
            ("preferred_username", json_str(username)),
            ("name", json_str(username.title())),
            ("email", json_str(f"{username}@example.test")),
            ("email_verified", json_bool(True)),
            (
                "realm_access",
                json_object(
                    (("roles", json_array(tuple(json_str(value) for value in role_values))),)
                ),
            ),
            ("groups", json_array(tuple(json_str(group) for group in groups))),
        ),
    )


def _oidc_env() -> tuple[tuple[str, str], ...]:
    """Return local IdP settings with create provisioning and team mapping."""
    return (
        ("TELESCREEN_OIDC_ISSUER", ISSUER),
        ("TELESCREEN_OIDC_CLIENT_ID", AUDIENCE),
        ("TELESCREEN_OIDC_AUDIENCE", AUDIENCE),
        ("TELESCREEN_OIDC_PROVISIONING", "create"),
        ("TELESCREEN_OIDC_TEAM_MAP", "security=secops"),
    )


@pytest.mark.asyncio
async def test_a1_oidc_roles_team_mapping_and_bound_token_intersection(tmp_path: Path) -> None:
    """Map four local IdP users and enforce role plus bound-token permission checks."""
    idp: LocalIdp = LocalIdp.generate(ISSUER, AUDIENCE)
    outbound: OutboundRecorder = OutboundRecorder()
    outbound.handlers.append(("idp.example.test", idp.mock_handler))
    world: E2EWorld = await make_world(tmp_path, _oidc_env(), outbound)
    try:
        team: httpx.Response = await world.client.post(
            "/api/teams",
            headers={"Authorization": f"Bearer {world.admin_token}"},
            json={"slug": "secops", "name": "Security Operations"},
        )
        assert team.status_code in (200, 201), team.text
        subjects: tuple[tuple[str, str, str | None], ...] = (
            ("sub-alice-v12", "alice", "admin"),
            ("sub-bob-v12", "bob", "responder"),
            ("sub-carol-v12", "carol", "viewer"),
            ("sub-dave-v12", "dave", None),
        )
        tokens: list[tuple[str, str]] = []
        for subject, username, role in subjects:
            token: str = idp.mint(_claims(subject, username, role, ("security",)))
            tokens.append((username, token))
            response: httpx.Response = await world.client.post(
                "/api/auth/session", headers={"Authorization": f"Bearer {token}"}
            )
            if role is None:
                assert response.status_code == 403
                assert response.json()["error"]["code"] == "no_role"
            else:
                assert response.status_code == 200, response.text
                assert response.json()["role"] == role
        users: httpx.Response = await world.client.get(
            "/api/users", headers={"Authorization": f"Bearer {world.admin_token}"}
        )
        assert users.status_code == 200
        assert '"handle":"alice"' in users.text and '"secops"' in users.text
        alice_token: str = next(value for username, value in tokens if username == "alice")
        alice_template: httpx.Response = await world.client.post(
            "/api/notification-templates",
            headers={"Authorization": f"Bearer {alice_token}"},
            json={
                "name": "e2e.alice-template",
                "event_kind": "workflow_notify",
                "subject": "Review {{ issue.short_id }}",
                "body": "A role-mapped administrator created this template.",
            },
        )
        assert alice_template.status_code == 201, alice_template.text

        collector: httpx.Response = await world.client.post(
            "/api/collectors",
            headers={"Authorization": f"Bearer {world.admin_token}"},
            json={
                "name": "v12-a1",
                "kind": "custom",
                "trigger_mode": "event",
                "cron": None,
                "sweep_cron": None,
                "selector_json": "{}",
                "credential_ref": None,
                "enabled": True,
            },
        )
        assert collector.status_code in (200, 201), collector.text
        async with world.database.sessions.begin() as session:
            ingest_token: CreatedToken = await TokenService(
                world.container.audit, world.clock
            ).create(session, "v12-a1-ingest", frozenset((ApiScope.INGEST,)), None)
        scan: httpx.Response = await world.client.post(
            "/api/ingest/v1/scan",
            headers={"Authorization": f"Bearer {ingest_token.token}"},
            json={
                "source": "custom:v12-a1",
                "resources": [
                    {
                        "locator": "safe.txt",
                        "segments": [{"locator": "L1", "text": github_token().value}],
                    }
                ],
            },
        )
        assert scan.status_code == 200, scan.text
        issue_id: str = scan.json()["issue_ids"][0]
        bob_patch: httpx.Response = await world.client.patch(
            f"/api/issues/{issue_id}",
            headers={
                "Authorization": f"Bearer {next(value for key, value in tokens if key == 'bob')}"
            },
            json={"assignee_ref": "member:alice"},
        )
        assert bob_patch.status_code == 200, bob_patch.text
        carol_patch: httpx.Response = await world.client.patch(
            f"/api/issues/{issue_id}",
            headers={
                "Authorization": f"Bearer {next(value for key, value in tokens if key == 'carol')}"
            },
            json={"assignee_ref": "member:alice"},
        )
        assert carol_patch.status_code == 403
        bound: httpx.Response = await world.client.post(
            "/api/tokens",
            headers={"Authorization": f"Bearer {world.admin_token}"},
            json={"label": "carol-write", "scopes": ["write"], "member_handle": "carol"},
        )
        assert bound.status_code == 201, bound.text
        bound_patch: httpx.Response = await world.client.patch(
            f"/api/issues/{issue_id}",
            headers={"Authorization": f"Bearer {bound.json()['token']}"},
            json={"assignee_ref": "member:alice"},
        )
        assert bound_patch.status_code == 403
    finally:
        await world.close()


@pytest.mark.asyncio
async def test_a2_idp_demotion_last_admin_and_cli_recovery(tmp_path: Path) -> None:
    """Protect manual last-admin removal and retain audited IdP demotion recovery."""
    idp: LocalIdp = LocalIdp.generate(ISSUER, AUDIENCE)
    outbound: OutboundRecorder = OutboundRecorder()
    outbound.handlers.append(("idp.example.test", idp.mock_handler))
    world: E2EWorld = await make_world(tmp_path, _oidc_env(), outbound)
    try:
        admin_token: str = idp.mint(_claims("sub-alice-a2", "alice", "admin", ()))
        sign_in: httpx.Response = await world.client.post(
            "/api/auth/session", headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert sign_in.status_code == 200
        protected: httpx.Response = await world.client.put(
            "/api/users/alice/role",
            headers={"Authorization": f"Bearer {world.admin_token}"},
            json={"role": None},
        )
        assert protected.status_code == 409
        demoted_token: str = idp.mint(_claims("sub-alice-a2", "alice", "responder", ()))
        demoted: httpx.Response = await world.client.post(
            "/api/auth/session", headers={"Authorization": f"Bearer {demoted_token}"}
        )
        assert demoted.status_code == 200
        assert demoted.json()["role"] == Role.RESPONDER.value
        async with world.database.sessions() as session:
            events: tuple[AuditRecordRow, ...] = tuple(
                await session.scalars(
                    select(AuditRecordRow).where(AuditRecordRow.action == "role.changed")
                )
            )
        assert any('"source":"idp"' in event.details_json for event in events)
        cli_args: argparse.Namespace = argparse.Namespace(
            command="member", member_command="set-role", handle="alice", role="admin"
        )
        assert await _run_async_command(world.container, cli_args) == 0
        async with world.database.sessions() as session:
            alice: MemberRow | None = await session.scalar(
                select(MemberRow).where(MemberRow.handle == "alice")
            )
            assert alice is not None and alice.role == Role.ADMIN.value
    finally:
        await world.close()
