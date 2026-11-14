"""End-to-end v1.2 notification template, set, and task escalation flows."""

import json
from datetime import timedelta
from email.message import EmailMessage
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from telescreen.core.json import JsonValue, json_str, parse_json
from telescreen.core.vocabulary import ApiScope, NotificationStatus
from telescreen.db.models import HumanTaskRow, NotificationRow
from telescreen.directory.tokens import CreatedToken, TokenService
from telescreen.notifications.email import SmtplibTransport
from tests.e2e.harness import E2EWorld, OutboundRecorder, admin_post, make_world
from tests.support.samples import aws_access_key_id


@pytest.mark.asyncio
async def test_t1_template_and_v2_notification_set_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Create a template and deliver one v2 set through Slack and captured SMTP."""
    slack_posts: list[str] = []
    email_messages: list[str] = []

    def slack_reply(request: httpx.Request) -> httpx.Response:
        body: str = request.content.decode("utf-8")
        slack_posts.append(body)
        return httpx.Response(
            200,
            request=request,
            json={"ok": True, "channel": "C0123ABCDE", "ts": "1.1"},
        )

    def capture_email(_transport: SmtplibTransport, message: EmailMessage) -> None:
        email_messages.append(message.get_content())

    monkeypatch.setattr(SmtplibTransport, "send", capture_email)
    outbound: OutboundRecorder = OutboundRecorder()
    outbound.handlers.append(("slack.com", slack_reply))
    world: E2EWorld = await make_world(
        tmp_path,
        (
            ("TELESCREEN_SLACK_BOT_TOKEN", "env:TELESCREEN_E2E_SLACK_TOKEN"),
            ("TELESCREEN_E2E_SLACK_TOKEN", "xoxb-e2e-test-token"),
        ),
        outbound,
    )
    try:
        member: httpx.Response = await world.client.post(
            "/api/members",
            headers={"Authorization": f"Bearer {world.admin_token}"},
            json={
                "handle": "mail-alerts",
                "display_name": "Mail Alerts",
                "email": "alerts@example.test",
            },
        )
        assert member.status_code == 201, member.text
        template: httpx.Response = await world.client.post(
            "/api/notification-templates",
            headers={"Authorization": f"Bearer {world.admin_token}"},
            json={
                "name": "e2e.workflow-alert",
                "event_kind": "workflow_notify",
                "subject": "Alert {{ issue.short_id }}",
                "body": "Review {{ issue.title }}",
            },
        )
        assert template.status_code == 201, template.text
        activated: httpx.Response = await world.client.post(
            "/api/notification-templates/e2e.workflow-alert/activate",
            headers={"Authorization": f"Bearer {world.admin_token}"},
            json={"version": 1},
        )
        assert activated.status_code == 200, activated.text
        source: str = """apiVersion: telescreen.workflow/v2
kind: Workflow
name: e2e-notification-set
notification_sets:
  - name: alerts
    targets:
      - recipients: [slack_channel:C0123ABCDE]
        channels: [slack]
        template: e2e.workflow-alert
      - recipients: [member:mail-alerts]
        channels: [email]
        template: e2e.workflow-alert
trigger:
  "on": [issue_created]
  match:
    min_severity: high
start: alert
steps:
  - id: alert
    type: notify
    notification_set: alerts
    next: done
  - id: done
    type: end
    result: succeeded
"""
        created: httpx.Response = await world.client.post(
            "/api/workflows",
            headers={"Authorization": f"Bearer {world.admin_token}"},
            json={"source_format": "yaml", "source_text": source},
        )
        assert created.status_code == 201, created.text
        active: httpx.Response = await world.client.post(
            "/api/workflows/e2e-notification-set/activate",
            headers={"Authorization": f"Bearer {world.admin_token}"},
            json={"version": 1},
        )
        assert active.status_code == 200, active.text
        started: httpx.Response = await world.client.post(
            "/api/workflows/e2e-notification-set/runs",
            headers={"Authorization": f"Bearer {world.admin_token}"},
            json={"issue_id": None},
        )
        assert started.status_code == 201, started.text
        await world.drain()
        run_id: str = started.json()["run_id"]
        run: httpx.Response = await world.client.get(
            f"/api/runs/{run_id}",
            headers={"Authorization": f"Bearer {world.admin_token}"},
        )
        assert run.status_code == 200, run.text
        run_document: JsonValue = parse_json(run.text)
        steps_value: JsonValue | None = run_document.get("steps")
        assert steps_value is not None
        steps: tuple[JsonValue, ...] | None = steps_value.as_list()
        assert steps is not None
        alert_step: JsonValue = next(
            item for item in steps if item.get("step_id") == json_str("alert")
        )
        assert alert_step.get("status") == json_str("succeeded")
        output_value: JsonValue | None = alert_step.get("output_json")
        assert output_value is not None and output_value.as_str() is not None
        output: JsonValue = parse_json(output_value.as_str() or "")
        targets_value: JsonValue | None = output.get("targets")
        queued: JsonValue | None = output.get("queued")
        assert queued is not None and queued.as_bool() is True
        assert targets_value is not None and targets_value.as_list() is not None
        target_outputs: tuple[JsonValue, ...] = targets_value.as_list() or ()
        queued_values: tuple[JsonValue | None, ...] = tuple(
            item.get("queued") for item in target_outputs
        )
        skipped_values: tuple[JsonValue | None, ...] = tuple(
            item.get("skipped") for item in target_outputs
        )
        assert all(item is not None for item in queued_values + skipped_values)
        assert tuple(item.as_int() for item in queued_values if item is not None) == (1, 1)
        assert tuple(item.as_int() for item in skipped_values if item is not None) == (0, 0)
        async with world.database.sessions() as session:
            rows: tuple[NotificationRow, ...] = tuple(
                await session.scalars(
                    select(NotificationRow).where(NotificationRow.set_name == "alerts")
                )
            )
        assert len(rows) == 2
        assert {row.target_index for row in rows} == {0, 1}
        assert all(row.status == NotificationStatus.SENT.value for row in rows), tuple(
            (row.channel, row.status, row.error_code, row.dlp_verdict) for row in rows
        ) + (
            tuple((request.host, request.path) for request in outbound.requests),
            tuple(outbound.failures),
        )
        assert all(row.template_name == "e2e.workflow-alert" for row in rows)
        assert len(slack_posts) == 1
        assert len(email_messages) == 1
    finally:
        await world.close()


@pytest.mark.asyncio
async def test_t2_human_task_notification_set_and_sla_escalation(tmp_path: Path) -> None:
    """Send human-task and FakeClock-triggered escalation notifications via a v2 set."""

    def slack_reply(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"ok": True, "channel": "C0123ABCDE", "ts": "1.2"},
        )

    outbound: OutboundRecorder = OutboundRecorder()
    outbound.handlers.append(("slack.com", slack_reply))
    world: E2EWorld = await make_world(
        tmp_path,
        (
            ("TELESCREEN_SLACK_BOT_TOKEN", "env:TELESCREEN_E2E_SLACK_TOKEN"),
            ("TELESCREEN_E2E_SLACK_TOKEN", "e2e-slack-token-placeholder"),
        ),
        outbound,
    )
    try:
        team: httpx.Response = await world.client.post(
            "/api/teams",
            headers={"Authorization": f"Bearer {world.admin_token}"},
            json={"slug": "secops", "name": "Security Operations"},
        )
        assert team.status_code in (200, 201), team.text
        collector: httpx.Response = await world.client.post(
            "/api/collectors",
            headers={"Authorization": f"Bearer {world.admin_token}"},
            json={
                "name": "v12-t2",
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
            ingest: CreatedToken = await TokenService(world.container.audit, world.clock).create(
                session, "v12-t2-ingest", frozenset((ApiScope.INGEST,)), None
            )
        scan: httpx.Response = await world.client.post(
            "/api/ingest/v1/scan",
            headers={"Authorization": f"Bearer {ingest.token}"},
            json={
                "source": "custom:v12-t2",
                "resources": [
                    {
                        "locator": "t2.txt",
                        "segments": [{"locator": "L1", "text": aws_access_key_id().value}],
                    }
                ],
            },
        )
        assert scan.status_code == 200, scan.text
        issue_id: str = scan.json()["issue_ids"][0]
        policy_text: str = (
            '{"name":"v12-t2-escalation","tiers":[{"after":"PT1S",'
            '"assign_to":"team:secops","notification_set":"task-alerts"}]}'
        )
        policy: httpx.Response = await admin_post(
            world,
            "/api/escalation-policies",
            json.dumps({"source_format": "json", "tiers_json": policy_text}),
        )
        assert policy.status_code == 201, policy.text
        source: str = """apiVersion: telescreen.workflow/v2
kind: Workflow
name: v12-t2-human-task
notification_sets:
  - name: task-alerts
    targets:
      - recipients: [slack_channel:C0123ABCDE]
        channels: [slack]
trigger:
  "on": [issue_created]
  match:
    min_severity: high
start: review
steps:
  - id: review
    type: human_task
    title: Review the escalated issue
    assignee: team:secops
    sla: PT1S
    escalation_policy: v12-t2-escalation
    notify_set: task-alerts
    outcomes:
      reviewed: done
  - id: done
    type: end
    result: succeeded
"""
        created: httpx.Response = await admin_post(
            world,
            "/api/workflows",
            json.dumps({"source_format": "yaml", "source_text": source}),
        )
        assert created.status_code == 201, created.text
        activated: httpx.Response = await world.client.post(
            "/api/workflows/v12-t2-human-task/activate",
            headers={"Authorization": f"Bearer {world.admin_token}"},
            json={"version": 1},
        )
        assert activated.status_code == 200, activated.text
        started: httpx.Response = await world.client.post(
            "/api/workflows/v12-t2-human-task/runs",
            headers={"Authorization": f"Bearer {world.admin_token}"},
            json={"issue_id": issue_id},
        )
        assert started.status_code == 201, started.text
        await world.drain()
        world.clock.advance(timedelta(seconds=5))
        await world.drain()
        async with world.database.sessions() as session:
            task: HumanTaskRow | None = await session.scalar(select(HumanTaskRow))
            rows: tuple[NotificationRow, ...] = tuple(
                await session.scalars(
                    select(NotificationRow).where(NotificationRow.set_name == "task-alerts")
                )
            )
        assert task is not None
        assert len(rows) >= 2
        assert {row.event_kind for row in rows} >= {
            "human_task_assigned",
            "sla_escalated",
        }
        assert all(row.status == NotificationStatus.SENT.value for row in rows)
        assert all(row.target_index == 0 for row in rows)
    finally:
        await world.close()
