"""End-to-end coverage for v1.2 CLI delivery and member utilities."""

import argparse
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from pydantic import TypeAdapter
from sqlalchemy import select

from telescreen.cli import _run_async_command, main
from telescreen.core.config import TelescreenSettings
from telescreen.core.vocabulary import Role, RoleChangeSource
from telescreen.db.models import ApiTokenRow, MemberRow
from telescreen.wiring import Container, build_container, close_container
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import aws_access_key_id


def test_workflow_validate_accepts_notification_set_email_targets() -> None:
    """Accept the documented v2 example with explicit email recipients."""
    assert main(["workflow", "validate", "examples/workflows/notification-sets.yaml"]) == 0


def test_workflow_validate_rejects_free_text_email_without_echoing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reject free-text email addresses while keeping CLI output value-free."""
    mailbox: str = "operator@example.invalid"
    workflow: Path = tmp_path / "email-body.yaml"
    workflow.write_text(
        """apiVersion: telescreen.workflow/v1
kind: Workflow
name: email-body-test
trigger:
  on: [issue_created]
start: alert
steps:
  - id: alert
    type: notify
    to: [email:ops@example.invalid]
    channel: email
    subject: Alert
    body: Contact operator@example.invalid
    next: completed
  - id: completed
    type: end
    result: succeeded
""",
        encoding="utf-8",
    )

    assert main(["workflow", "validate", str(workflow)]) == 2
    output: str = capsys.readouterr().out
    assert "raw_value_detected" in output
    assert mailbox not in output

    secret: str = aws_access_key_id().value
    secret_workflow: Path = tmp_path / "secret-body.yaml"
    secret_workflow.write_text(
        """apiVersion: telescreen.workflow/v1
kind: Workflow
name: secret-body-test
trigger:
  on: [issue_created]
start: alert
steps:
  - id: alert
    type: notify
    to: [email:ops@example.invalid]
    channel: email
    subject: Alert
    body: SECRET_VALUE
    next: completed
  - id: completed
    type: end
    result: succeeded
""".replace("SECRET_VALUE", secret),
        encoding="utf-8",
    )

    assert main(["workflow", "validate", str(secret_workflow)]) == 2
    secret_output: str = capsys.readouterr().out
    assert "raw_value_detected" in secret_output
    assert secret not in secret_output


@dataclass(frozen=True)
class _CliRow:
    """Safe JSON output from one notification CLI row."""

    notification_id: str
    channel: str
    status: str
    dlp_verdict: str
    error_code: str | None


@dataclass(frozen=True)
class _TestEnv:
    """Resolve test-only credentials without exposing them to output."""

    def get(self, key: str, /) -> str | None:
        """Return the fingerprint key and Slack token by their configured names."""
        return {
            "TELESCREEN_TEST_KEY": "x" * 32,
            "TELESCREEN_SLACK_TEST_TOKEN": "xoxb-test-token",
        }.get(key)


@pytest.mark.asyncio
async def test_cli_v12_notification_delivery_roles_and_member_bound_tokens(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exercise notify, role recovery and token binding through the real services."""

    def slack_reply(request: httpx.Request) -> httpx.Response:
        """Return Slack's successful chat.postMessage response."""
        return httpx.Response(
            200,
            request=request,
            json={"ok": True, "channel": "C0123ABCDE", "ts": "1.1"},
        )

    database: SqliteHarness = await make_sqlite_harness()
    env: _TestEnv = _TestEnv()
    outbound: httpx.AsyncClient = httpx.AsyncClient(transport=httpx.MockTransport(slack_reply))
    settings: TelescreenSettings = TelescreenSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        fingerprint_key_ref="env:TELESCREEN_TEST_KEY",
        slack_bot_token_ref="env:TELESCREEN_SLACK_TEST_TOKEN",
    )
    container: Container = build_container(settings, env, outbound, database.sessions)
    try:
        notify_args: argparse.Namespace = argparse.Namespace(
            command="notify",
            notify_command="test",
            channel="slack",
            recipient="slack_channel:C0123ABCDE",
            template=None,
        )
        assert await _run_async_command(container, notify_args) == 0
        sent_line: str = capsys.readouterr().out.strip()
        sent_row: _CliRow = TypeAdapter(_CliRow).validate_json(sent_line)
        assert sent_row.status == "sent"
        assert sent_row.channel == "slack"
        unknown_args: argparse.Namespace = argparse.Namespace(
            command="notify",
            notify_command="test",
            channel="slack",
            recipient="slack_channel:C0123ABCDE",
            template="missing.template",
        )
        assert await _run_async_command(container, unknown_args) == 2
        assert "unknown template" in capsys.readouterr().err
        unreachable_args: argparse.Namespace = argparse.Namespace(
            command="notify",
            notify_command="test",
            channel="slack",
            recipient="email:unreachable@example.test",
            template=None,
        )
        assert await _run_async_command(container, unreachable_args) == 1
        skipped_line: str = capsys.readouterr().out.strip()
        skipped: _CliRow = TypeAdapter(_CliRow).validate_json(skipped_line)
        assert skipped.status == "skipped"
        async with database.sessions.begin() as session:
            member: MemberRow = await container.directory.create_member(
                session, "last-admin", "Last Admin", None, None
            )
            await container.v12.roles.set_role(
                session,
                member.handle,
                Role.ADMIN,
                "system:test",
                RoleChangeSource.MANUAL,
            )
        role_args: argparse.Namespace = argparse.Namespace(
            command="member",
            member_command="set-role",
            handle="last-admin",
            role="none",
        )
        assert await _run_async_command(container, role_args) == 1
        assert "last admin cannot be removed" in capsys.readouterr().err
        unknown_role_args: argparse.Namespace = argparse.Namespace(
            command="member",
            member_command="set-role",
            handle="missing-member",
            role="viewer",
        )
        assert await _run_async_command(container, unknown_role_args) == 1
        assert "member role could not be changed" in capsys.readouterr().err
        unknown_token_args: argparse.Namespace = argparse.Namespace(
            command="token",
            token_command="create",
            label="unknown-member",
            scopes="read",
            member="missing-member",
        )
        assert await _run_async_command(container, unknown_token_args) == 2
        assert "unknown member" in capsys.readouterr().err
        token_args: argparse.Namespace = argparse.Namespace(
            command="token",
            token_command="create",
            label="member-bound",
            scopes="read",
            member="last-admin",
        )
        assert await _run_async_command(container, token_args) == 0
        assert capsys.readouterr().out.strip()
        async with database.sessions() as session:
            token: ApiTokenRow | None = await session.scalar(select(ApiTokenRow))
        assert token is not None
        assert token.member_id == member.id
    finally:
        await close_container(container)
        await database.close()
        await outbound.aclose()
