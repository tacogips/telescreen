"""Exercise v1.2 notification, role, token, and OIDC paths in the real-container leak matrix."""

import argparse

import httpx
from sqlalchemy import func, select

from telescreen.cli import _run_async_command
from telescreen.core.json import JsonValue, dump_json, json_array, json_object, json_str, parse_json
from telescreen.core.vocabulary import NotificationChannel, NotificationEvent
from telescreen.db.models import AuditRecordRow, NotificationRow, NotificationTemplateRow
from telescreen.ports.notifications import NotificationRequest
from tests.identity.oidc_fixtures import claims_document
from tests.leak.harness import LeakWorld, auth
from tests.support.oidc import TestIdp as LocalTestIdp
from tests.support.samples import Sample, all_samples, github_token


async def template_rejections(world: LeakWorld) -> None:
    """Reject every sample in each text field and retain only field/code audit details."""
    names: tuple[str, ...] = ("subject", "body", "email_subject", "email_body", "slack_body")
    for sample_index, sample in enumerate(all_samples()):
        for field_name in names:
            sample_text: str = "secret=" + sample.value
            texts: tuple[tuple[str, JsonValue], ...] = tuple(
                (
                    candidate,
                    json_str(sample_text if field_name == candidate else f"safe {candidate}"),
                )
                for candidate in names
            )
            create_fields: tuple[tuple[str, JsonValue], ...] = (
                ("name", json_str(f"leak-v12-{sample_index}-{field_name}")),
                ("event_kind", json_str("workflow_notify")),
                *texts,
            )
            response: httpx.Response = await world.client.post(
                "/api/notification-templates",
                headers=auth(world.admin_token) + (("Content-Type", "application/json"),),
                content=dump_json(json_object(create_fields)),
            )
            world.recorder.responses.append(response.text)
            assert response.status_code == 422, (sample.name, field_name, response.text)
            assert sample.value not in response.text
            assert field_name in response.text
            validation: httpx.Response = await world.client.post(
                "/api/notification-templates/validate",
                headers=auth(world.admin_token),
                json={
                    "event_kind": "workflow_notify",
                    "subject": sample_text if field_name == "subject" else "safe subject",
                    "body": sample_text if field_name == "body" else "safe body",
                    "email_subject": (
                        sample_text if field_name == "email_subject" else "safe email_subject"
                    ),
                    "email_body": (
                        sample_text if field_name == "email_body" else "safe email_body"
                    ),
                    "slack_body": sample_text if field_name == "slack_body" else "safe slack_body",
                },
            )
            world.recorder.responses.append(validation.text)
            assert validation.status_code == 200, validation.text
            assert '"valid":false' in validation.text
            assert field_name in validation.text and sample.value not in validation.text
            preview: httpx.Response = await world.client.post(
                "/api/notification-templates/preview",
                headers=auth(world.admin_token),
                json={
                    "event_kind": "workflow_notify",
                    "subject": sample_text if field_name == "subject" else "safe subject",
                    "body": sample_text if field_name == "body" else "safe body",
                    "email_subject": (
                        sample_text if field_name == "email_subject" else "safe email_subject"
                    ),
                    "email_body": (
                        sample_text if field_name == "email_body" else "safe email_body"
                    ),
                    "slack_body": sample_text if field_name == "slack_body" else "safe slack_body",
                },
            )
            world.recorder.responses.append(preview.text)
            assert preview.status_code == 422, preview.text
            assert sample.value not in preview.text
    async with world.database.sessions() as session:
        template_count: int = (
            await session.scalar(select(func.count()).select_from(NotificationTemplateRow)) or 0
        )
        rejected_audits: tuple[AuditRecordRow, ...] = tuple(
            await session.scalars(
                select(AuditRecordRow).where(AuditRecordRow.action == "template.rejected")
            )
        )
    assert template_count == 0
    assert rejected_audits
    assert all(
        any(field_name in row.details_json for row in rejected_audits) for field_name in names
    )
    assert all(
        sample.value not in row.details_json for row in rejected_audits for sample in all_samples()
    )


async def notification_set_flow(world: LeakWorld, smtp_messages: list[str]) -> None:
    """Run v2 Slack/email fan-out with masked issue text and an unreachable skip."""
    sample: Sample = github_token()
    collector: httpx.Response = await world.client.post(
        "/api/collectors",
        headers=auth(world.admin_token),
        json={
            "name": "v12-notification-leak",
            "kind": "custom",
            "trigger_mode": "event",
            "cron": None,
            "sweep_cron": None,
            "selector_json": "{}",
            "credential_ref": None,
            "enabled": True,
        },
    )
    world.recorder.responses.append(collector.text)
    assert collector.status_code in (200, 201), collector.text
    scanned: httpx.Response = await world.client.post(
        "/api/ingest/v1/scan",
        headers=auth(world.ingest_token),
        json={
            "source": "custom:v12-notification-leak",
            "resources": [
                {
                    "locator": "notification.txt",
                    "segments": [{"locator": "L1", "text": sample.value}],
                }
            ],
        },
    )
    world.recorder.responses.append(scanned.text)
    assert scanned.status_code == 200, scanned.text
    issue_id: str = scanned.json()["issue_ids"][0]
    source: str = """apiVersion: telescreen.workflow/v2
kind: Workflow
name: leak-v12-notification-set
notification_sets:
  - name: leak-alerts
    targets:
      - recipients: [slack_channel:C0123ABCDE]
        channels: [slack]
      - recipients: [email:secops-leads@example.invalid]
        channels: [email]
      - recipients: [member:missing-v12-recipient]
        channels: [slack]
trigger:
  "on": [issue_created]
  match:
    min_severity: high
start: alert
steps:
  - id: alert
    type: notify
    notification_set: leak-alerts
    subject: Leak check
    body: Review masked issue {{ issue.title }}.
    next: done
  - id: done
    type: end
    result: succeeded
"""
    created: httpx.Response = await world.client.post(
        "/api/workflows",
        headers=auth(world.admin_token),
        json={"source_format": "yaml", "source_text": source},
    )
    world.recorder.responses.append(created.text)
    assert created.status_code == 201, created.text
    activated: httpx.Response = await world.client.post(
        "/api/workflows/leak-v12-notification-set/activate",
        headers=auth(world.admin_token),
        json={"version": 1},
    )
    world.recorder.responses.append(activated.text)
    assert activated.status_code == 200, activated.text
    run: httpx.Response = await world.client.post(
        "/api/workflows/leak-v12-notification-set/runs",
        headers=auth(world.admin_token),
        json={"issue_id": issue_id},
    )
    world.recorder.responses.append(run.text)
    assert run.status_code == 201, run.text
    run_id: str = run.json()["run_id"]
    await world.drain()
    run_detail: httpx.Response = await world.client.get(
        f"/api/runs/{run_id}", headers=auth(world.admin_token)
    )
    world.recorder.responses.append(run_detail.text)
    assert run_detail.status_code == 200, run_detail.text
    run_document: JsonValue = parse_json(run_detail.text)
    step_values: JsonValue | None = run_document.get("steps")
    assert step_values is not None and step_values.as_list() is not None
    step_rows: tuple[JsonValue, ...] = step_values.as_list() or ()
    step: JsonValue = next(item for item in step_rows if item.get("step_id") == json_str("alert"))
    output_value: JsonValue | None = step.get("output_json")
    assert output_value is not None
    output_text: str | None = output_value.as_str()
    assert output_text is not None
    output: JsonValue = parse_json(output_text)
    targets_value: JsonValue | None = output.get("targets")
    assert targets_value is not None and targets_value.as_list() is not None
    target_outputs: tuple[JsonValue, ...] = targets_value.as_list() or ()
    assert len(target_outputs) == 3
    queued_values: tuple[JsonValue | None, ...] = tuple(
        item.get("queued") for item in target_outputs[:2]
    )
    skipped_value: JsonValue | None = target_outputs[2].get("skipped")
    assert all(item is not None for item in queued_values) and skipped_value is not None
    assert tuple(item.as_int() for item in queued_values if item is not None) == (1, 1)
    assert skipped_value.as_int() == 1
    async with world.database.sessions() as session:
        rows: tuple[NotificationRow, ...] = tuple(
            await session.scalars(
                select(NotificationRow).where(NotificationRow.set_name == "leak-alerts")
            )
        )
    assert len(rows) == 3
    assert any(row.status == "skipped" for row in rows)
    assert {row.channel for row in rows} == {"slack", "email"}
    assert all(sample.value not in row.subject + row.body for row in rows)
    assert any("Review masked issue" in message for message in smtp_messages)
    assert any(
        "slack.com" in url and "Review masked issue" in body.decode("utf-8", errors="replace")
        for url, body in world.recorder.requests
    )


async def inline_notification_flow(world: LeakWorld) -> None:
    """Mask a runtime sample through the notification service's inline event path."""
    sample: Sample = github_token()
    async with world.database.sessions.begin() as session:
        await world.container.v12.notifications.send(
            session,
            NotificationRequest(
                NotificationChannel.SLACK,
                ("slack_channel:C0123ABCDE",),
                "Leak check",
                sample.value,
                "system:leak-test",
                event=NotificationEvent.WORKFLOW_NOTIFY,
                inline=True,
                set_name="leak-alerts-inline",
                target_index=0,
            ),
        )
    await world.drain()


async def role_and_token_flow(world: LeakWorld) -> None:
    """Change a member role, reject last-admin removal, and create/revoke a token."""
    created: httpx.Response = await world.client.post(
        "/api/members",
        headers=auth(world.admin_token),
        json={"handle": "v12-leak-user", "display_name": "Leak Test User", "email": None},
    )
    world.recorder.responses.append(created.text)
    assert created.status_code == 201, created.text
    changed: httpx.Response = await world.client.put(
        "/api/users/v12-leak-user/role",
        headers=auth(world.admin_token),
        json={"role": "admin"},
    )
    world.recorder.responses.append(changed.text)
    assert changed.status_code == 200
    rejected: httpx.Response = await world.client.put(
        "/api/users/v12-leak-user/role", headers=auth(world.admin_token), json={"role": "none"}
    )
    world.recorder.responses.append(rejected.text)
    assert rejected.status_code == 409
    token: httpx.Response = await world.client.post(
        "/api/tokens",
        headers=auth(world.admin_token),
        json={"label": "v12-leak-token", "scopes": ["read"], "member_handle": "v12-leak-user"},
    )
    world.recorder.responses.append(token.text)
    assert token.status_code == 201
    value: str = token.json()["token"]
    revoked: httpx.Response = await world.client.post(
        f"/api/tokens/{token.json()['id']}/revoke", headers=auth(world.admin_token)
    )
    world.recorder.responses.append(revoked.text)
    assert revoked.status_code == 204
    assert value not in revoked.text


async def oidc_sign_in_flow(world: LeakWorld, idp: LocalTestIdp) -> tuple[str, ...]:
    """Exercise admin/viewer, no-role and invalid OIDC tokens without retaining claims."""
    admin_claims: str = claims_document(
        world.clock.now(),
        (
            ("sub", json_str("runtime-sub-admin-v12")),
            ("preferred_username", json_str("oidc-admin-v12")),
            ("email", json_str("oidc-admin-v12@example.test")),
            (
                "realm_access",
                json_object((("roles", json_array((json_str("telescreen-admin"),))),)),
            ),
        ),
    )
    viewer_claims: str = claims_document(
        world.clock.now(),
        (
            ("sub", json_str("runtime-sub-viewer-v12")),
            ("preferred_username", json_str("oidc-viewer-v12")),
            ("email", json_str("oidc-viewer-v12@example.test")),
            (
                "realm_access",
                json_object((("roles", json_array((json_str("telescreen-viewer"),))),)),
            ),
        ),
    )
    no_role_claims: str = claims_document(
        world.clock.now(),
        (
            ("sub", json_str("runtime-sub-none-v12")),
            ("preferred_username", json_str("oidc-none-v12")),
            ("email", json_str("oidc-none-v12@example.test")),
            ("realm_access", json_object((("roles", json_array(())),))),
        ),
    )
    admin_token: str = idp.mint(admin_claims)
    viewer_token: str = idp.mint(viewer_claims)
    no_role_token: str = idp.mint(no_role_claims)
    id_claims: str = claims_document(
        world.clock.now(),
        (
            ("sub", json_str("runtime-sub-id-token-v12")),
            ("typ", json_str("ID")),
            ("email", json_str("oidc-id-token-v12@example.test")),
        ),
    )
    id_token: str = idp.mint(id_claims)
    invalid_token: str = "invalid-token-v12"
    for token_value in (admin_token, viewer_token):
        response: httpx.Response = await world.client.post(
            "/api/auth/session", headers=auth(token_value)
        )
        world.recorder.responses.append(response.text)
        assert response.status_code == 200
    no_role: httpx.Response = await world.client.post(
        "/api/auth/session", headers=auth(no_role_token)
    )
    world.recorder.responses.append(no_role.text)
    assert no_role.status_code == 403
    id_token_response: httpx.Response = await world.client.post(
        "/api/auth/session", headers=auth(id_token)
    )
    world.recorder.responses.append(id_token_response.text)
    assert id_token_response.status_code == 401
    invalid: httpx.Response = await world.client.post(
        "/api/auth/session", headers=auth(invalid_token)
    )
    world.recorder.responses.append(invalid.text)
    assert invalid.status_code == 401
    return (
        admin_token,
        viewer_token,
        no_role_token,
        id_token,
        invalid_token,
        "runtime-sub-admin-v12",
        "runtime-sub-viewer-v12",
        "runtime-sub-none-v12",
        "runtime-sub-id-token-v12",
    )


async def notify_test_flow(world: LeakWorld) -> None:
    """Run the notify-test CLI and the same service path with a runtime sample."""
    args: argparse.Namespace = argparse.Namespace(
        command="notify",
        notify_command="test",
        channel="slack",
        recipient="slack_channel:C0123ABCDE",
        template=None,
    )
    assert await _run_async_command(world.container, args) == 0
    sample: Sample = github_token()
    async with world.database.sessions.begin() as session:
        await world.container.v12.notifications.send(
            session,
            NotificationRequest(
                NotificationChannel.SLACK,
                ("slack_channel:C0123ABCDE",),
                "Telescreen notify-test leak check",
                sample.value,
                "system:cli",
                event=NotificationEvent.WORKFLOW_NOTIFY,
                inline=True,
            ),
        )
    await world.drain()


async def run_v12_flows(
    world: LeakWorld, idp: LocalTestIdp | None, smtp_messages: list[str]
) -> tuple[str, ...]:
    """Run assigned leak flows and return any secrets that need literal assertions."""
    await template_rejections(world)
    await notification_set_flow(world, smtp_messages)
    await inline_notification_flow(world)
    await role_and_token_flow(world)
    tokens: tuple[str, ...] = await oidc_sign_in_flow(world, idp) if idp is not None else ()
    await notify_test_flow(world)
    return tokens
