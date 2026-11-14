"""Exercise v1.3 issue, activity, alert, and organization paths for leak checks."""

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ApiScope, IssueEventKind, RuleClass, Severity
from telescreen.db.models import ExternalIssueLinkRow, IssueRow, MemberRow
from telescreen.directory.tokens import CreatedToken, TokenService
from telescreen.issues.external.github import GithubIssueCreator
from telescreen.notifications.guard import DlpOutboundGuard
from telescreen.notifications.sender import NotificationService
from telescreen.ports.issues import IssueEvent
from telescreen.ports.notifications import GuardVerdict
from tests.leak.harness import LeakWorld, auth
from tests.leak.sinks import (
    api_responses,
    database_text,
    logs,
    outbound_bodies,
    search_documents,
)
from tests.support.samples import Sample, all_samples, find_leaks


async def run_v13_flows(world: LeakWorld, smtp_messages: list[str]) -> tuple[str, ...]:
    """Exercise the v1.3 API surfaces and return runtime samples used by the flows."""
    samples: tuple[Sample, ...] = all_samples()
    collector: httpx.Response = await world.client.post(
        "/api/collectors",
        headers=auth(world.admin_token),
        json={
            "name": "v13-leak-matrix",
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
    response: httpx.Response = await world.client.post(
        "/api/ingest/v1/scan",
        headers=auth(world.ingest_token),
        json={
            "source": "custom:v13-leak-matrix",
            "resources": [
                {
                    "locator": f"safe-resource-{index}.txt",
                    "segments": [{"locator": "line:1", "text": sample.value}],
                }
                for index, sample in enumerate(samples)
            ],
        },
    )
    world.recorder.responses.append(response.text)
    assert response.status_code == 200, response.text
    issue_ids: list[str] = response.json()["issue_ids"]
    assert issue_ids
    issue_id: str = issue_ids[0]
    sample: Sample = samples[0]
    member_token: str = await _member_token(world)
    await _flow_1_issue_surfaces(world, issue_id)
    await _assert_flow_sinks(world, samples, smtp_messages)
    await _flow_2_saved_view(world, member_token, sample)
    await _assert_flow_sinks(world, samples, smtp_messages)
    await _flow_3_comment_and_activity(world, issue_id, member_token, sample, smtp_messages)
    await _assert_flow_sinks(world, samples, smtp_messages)
    await _flow_4_external_issue(world, issue_id, sample)
    await _assert_flow_sinks(world, samples, smtp_messages)
    await _flow_5_alert_rule(world, issue_id, sample)
    await _assert_flow_sinks(world, samples, smtp_messages)
    await _flow_5_detail_and_aggregates(world, issue_id)
    await _assert_flow_sinks(world, samples, smtp_messages)
    await _flow_7_bulk_and_merge_routes(world, issue_id, tuple(issue_ids[1:]))
    await _assert_flow_sinks(world, samples, smtp_messages)
    assert len(issue_ids) >= 5
    await _flow_8_archive_state(world, issue_ids[4])
    await _assert_flow_sinks(world, samples, smtp_messages)
    await _flow_8_organization(world, member_token, sample)
    await _assert_flow_sinks(world, samples, smtp_messages)
    await _flow_5_weekly_report(world, smtp_messages)
    await _assert_flow_sinks(world, samples, smtp_messages)
    await _flow_9_invalid_query(world, sample)
    await world.drain()
    await _assert_flow_sinks(world, samples, smtp_messages)
    return tuple(item.value for item in samples)


async def _flow_1_issue_surfaces(world: LeakWorld, issue_id: str) -> None:
    """Read issue list, selectors, tags, stats, and activity after ingestion."""
    paths: tuple[str, ...] = (
        "/api/issues?query=is:unresolved",
        f"/api/issues/{issue_id}/events",
        f"/api/issues/{issue_id}/events/oldest",
        f"/api/issues/{issue_id}/tags",
        f"/api/issues/{issue_id}/stats",
        f"/api/issues/{issue_id}/activity",
    )
    for path in paths:
        response: httpx.Response = await world.client.get(path, headers=auth(world.admin_token))
        world.recorder.responses.append(response.text)
        assert response.status_code == 200, response.text


async def _flow_2_saved_view(world: LeakWorld, member_token: str, sample: Sample) -> None:
    """Verify raw monitored text in persisted view fields is rejected safely."""
    response: httpx.Response = await world.client.post(
        "/api/views",
        headers=auth(member_token),
        json={
            "name": "safe view",
            "query": sample.value,
            "sort": "last_seen",
            "period": None,
            "visibility": "personal",
        },
    )
    world.recorder.responses.append(response.text)
    assert response.status_code == 422, response.text
    assert sample.value not in response.text
    for visibility in ("personal", "shared"):
        valid: httpx.Response = await world.client.post(
            "/api/views",
            headers=auth(member_token),
            json={
                "name": "safe " + visibility,
                "query": "is:unresolved",
                "sort": "last_seen",
                "period": None,
                "visibility": visibility,
            },
        )
        world.recorder.responses.append(valid.text)
        assert valid.status_code == 201, valid.text
    listed: httpx.Response = await world.client.get("/api/views", headers=auth(member_token))
    world.recorder.responses.append(listed.text)
    assert listed.status_code == 200, listed.text


async def _flow_3_comment_and_activity(
    world: LeakWorld,
    issue_id: str,
    member_token: str,
    sample: Sample,
    smtp_messages: list[str],
) -> None:
    """Exercise comment masking and the persisted activity response."""
    response: httpx.Response = await world.client.post(
        f"/api/issues/{issue_id}/comments",
        headers=auth(world.admin_token),
        json={"body": "Review " + sample.value},
    )
    world.recorder.responses.append(response.text)
    assert response.status_code == 201, response.text
    assert sample.value not in response.text
    activity: httpx.Response = await world.client.get(
        f"/api/issues/{issue_id}/activity", headers=auth(world.admin_token)
    )
    world.recorder.responses.append(activity.text)
    assert activity.status_code == 200, activity.text
    assert sample.value not in activity.text
    handle: str = "leak-v13-user"
    mention: httpx.Response = await world.client.post(
        f"/api/issues/{issue_id}/comments",
        headers=auth(world.admin_token),
        json={"body": f"Review @{handle} {sample.value}"},
    )
    world.recorder.responses.append(mention.text)
    assert mention.status_code == 201, mention.text
    comment_id: str = mention.json()["id"]
    edited: httpx.Response = await world.client.patch(
        f"/api/issues/{issue_id}/comments/{comment_id}",
        headers=auth(world.admin_token),
        json={"body": f"Updated @{handle} {sample.value}"},
    )
    world.recorder.responses.append(edited.text)
    assert edited.status_code == 200, edited.text
    assert sample.value not in edited.text
    for path, body in (
        (f"/api/issues/{issue_id}/subscription", {"subscribed": True}),
        (f"/api/issues/{issue_id}/bookmark", {}),
    ):
        followed: httpx.Response = await world.client.put(
            path, headers=auth(member_token), json=body
        )
        world.recorder.responses.append(followed.text)
        assert followed.status_code == 200, followed.text
    follow_activity: httpx.Response = await world.client.get(
        f"/api/issues/{issue_id}/activity", headers=auth(world.admin_token)
    )
    world.recorder.responses.append(follow_activity.text)
    assert follow_activity.status_code == 200, follow_activity.text
    await world.drain()
    assert any("TELE-" in message for message in smtp_messages)


async def _flow_4_external_issue(world: LeakWorld, issue_id: str, sample: Sample) -> None:
    """Exercise external issue links with user-controlled titles."""
    response: httpx.Response = await world.client.post(
        f"/api/issues/{issue_id}/external-issues",
        headers=auth(world.admin_token),
        json={"url": "https://tracker.example.invalid/ticket/1", "title": sample.value},
    )
    world.recorder.responses.append(response.text)
    assert response.status_code == 422, response.text
    assert sample.value not in response.text
    issue: IssueRow | None
    async with world.database.sessions() as session:
        issue = await session.get(IssueRow, issue_id)
    assert issue is not None
    requested: list[bytes] = []

    def capture(request: httpx.Request) -> httpx.Response:
        requested.append(request.content)
        return httpx.Response(201, json={"number": 71, "html_url": "https://github.invalid/1"})

    assert isinstance(world.container.v13.alert_evaluator.sender, NotificationService)
    creator: GithubIssueCreator = GithubIssueCreator(
        httpx.MockTransport(capture),
        "https://api.github.invalid",
        "env:TELESCREEN_GITHUB_TOKEN",
        ("safe/repo",),
        world.container.env,
        DlpOutboundGuard(world.container.v13.alert_evaluator.sender.gate),
        world.container.redactor,
        world.container.audit,
        world.clock,
    )
    async with world.database.sessions.begin() as session:
        issue = await session.get(IssueRow, issue_id)
        assert issue is not None
        created: ExternalIssueLinkRow = await creator.create(
            session, issue, "safe/repo", None, "token:leak-test"
        )
        assert created.kind == "github"
    assert len(requested) == 1
    world.recorder.requests.append(
        ("https://api.github.invalid/repos/safe/repo/issues", requested[0])
    )

    class BlockingGuard:
        async def inspect(self, text: str) -> GuardVerdict:
            del text
            return GuardVerdict(False, 1)

    blocked_creator: GithubIssueCreator = GithubIssueCreator(
        httpx.MockTransport(capture),
        "https://api.github.invalid",
        "env:TELESCREEN_GITHUB_TOKEN",
        ("safe/repo",),
        world.container.env,
        BlockingGuard(),
        world.container.redactor,
        world.container.audit,
        world.clock,
    )
    before: int = len(requested)
    try:
        async with world.database.sessions.begin() as session:
            issue = await session.get(IssueRow, issue_id)
            assert issue is not None
            await blocked_creator.create(session, issue, "safe/repo", None, "token:leak-test")
    except TelescreenError as error:
        assert error.detail == "dlp_blocked"
    else:
        raise AssertionError("DLP guard must block the GitHub create request")
    assert len(requested) == before


async def _flow_5_alert_rule(world: LeakWorld, issue_id: str, sample: Sample) -> None:
    """Exercise alert rule validation with a sensitive user-controlled rule name."""
    response: httpx.Response = await world.client.post(
        "/api/alert-rules/validate",
        headers=auth(world.admin_token),
        json={
            "name": sample.value,
            "trigger": {"kind": "issue_created"},
            "filters": {},
            "actions": {},
            "action_interval_minutes": 60,
        },
    )
    world.recorder.responses.append(response.text)
    assert response.status_code == 200, response.text
    assert sample.value not in response.text
    rejected: httpx.Response = await world.client.post(
        "/api/alert-rules",
        headers=auth(world.admin_token),
        json={
            "name": sample.value,
            "trigger": {"kind": "issue_created"},
            "filters": {},
            "actions": {},
            "action_interval_minutes": 60,
        },
    )
    world.recorder.responses.append(rejected.text)
    assert rejected.status_code == 422, rejected.text
    assert sample.value not in rejected.text
    valid: httpx.Response = await world.client.post(
        "/api/alert-rules",
        headers=auth(world.admin_token),
        json={
            "name": "v13-safe-issue-created",
            "trigger": {"kind": "issue_created"},
            "filters": {},
            "actions": {
                "targets": [
                    {"recipients": ["email:leak-v13@example.invalid"], "channels": ["email"]}
                ]
            },
            "action_interval_minutes": 60,
        },
    )
    world.recorder.responses.append(valid.text)
    assert valid.status_code == 201, valid.text
    alert_id: str = valid.json()["id"]
    scanned: httpx.Response = await world.client.post(
        "/api/ingest/v1/scan",
        headers=auth(world.ingest_token),
        json={
            "source": "custom:v13-leak-matrix",
            "resources": [
                {
                    "locator": "new-safe-resource.txt",
                    "segments": [{"locator": "L1", "text": all_samples()[1].value}],
                }
            ],
        },
    )
    world.recorder.responses.append(scanned.text)
    assert scanned.status_code == 200, scanned.text
    async with world.database.sessions.begin() as session:
        issue: IssueRow | None = await session.get(IssueRow, issue_id)
        assert issue is not None
        await world.container.v13.alert_evaluator.on_issue_event(
            session,
            IssueEvent(
                IssueEventKind.ISSUE_CREATED,
                "v13-leak-alert-event",
                issue.id,
                issue.source,
                RuleClass(issue.rule_class),
                Severity(issue.severity),
                world.clock.now(),
            ),
        )
    await world.drain()
    history: httpx.Response = await world.client.get(
        f"/api/alert-rules/{alert_id}/history", headers=auth(world.admin_token)
    )
    world.recorder.responses.append(history.text)
    assert history.status_code == 200, history.text
    assert "items" in history.text and history.json()["items"]


async def _flow_5_detail_and_aggregates(world: LeakWorld, issue_id: str) -> None:
    """Read aggregate surfaces that must contain only masked issue projections."""
    for path in ("/api/dashboard?period=7d", f"/api/issues/{issue_id}/stats"):
        response: httpx.Response = await world.client.get(path, headers=auth(world.admin_token))
        world.recorder.responses.append(response.text)
        assert response.status_code == 200, response.text


async def _flow_7_bulk_and_merge_routes(
    world: LeakWorld, issue_id: str, remaining_issue_ids: tuple[str, ...]
) -> None:
    """Exercise bulk route validation without changing the ingested sample value."""
    response: httpx.Response = await world.client.post(
        "/api/issues/bulk",
        headers=auth(world.admin_token),
        json={"action": "priority", "ids": [issue_id], "params": {"priority": "high"}},
    )
    world.recorder.responses.append(response.text)
    assert response.status_code == 200, response.text
    resolved: httpx.Response = await world.client.post(
        "/api/issues/bulk",
        headers=auth(world.admin_token),
        json={
            "action": "resolve",
            "ids": [issue_id],
            "params": {"reason": "false_positive", "note": "safe review"},
        },
    )
    world.recorder.responses.append(resolved.text)
    assert resolved.status_code == 200, resolved.text
    assert len(remaining_issue_ids) >= 3
    primary_id: str = remaining_issue_ids[0]
    child_id: str = remaining_issue_ids[1]
    archive_id: str = remaining_issue_ids[2]
    bulk_archive: httpx.Response = await world.client.post(
        "/api/issues/bulk",
        headers=auth(world.admin_token),
        json={
            "action": "archive",
            "ids": [archive_id],
            "params": {"archive": {"kind": "for_duration", "duration_minutes": 30}},
        },
    )
    world.recorder.responses.append(bulk_archive.text)
    assert bulk_archive.status_code == 200, bulk_archive.text
    merge: httpx.Response = await world.client.post(
        "/api/issues/merge",
        headers=auth(world.admin_token),
        json={"primary_id": primary_id, "ids": [child_id]},
    )
    world.recorder.responses.append(merge.text)
    assert merge.status_code == 200, merge.text
    unmerge: httpx.Response = await world.client.post(
        f"/api/issues/{primary_id}/unmerge",
        headers=auth(world.admin_token),
        json={"ids": [child_id]},
    )
    world.recorder.responses.append(unmerge.text)
    assert unmerge.status_code == 200, unmerge.text


async def _flow_8_archive_state(world: LeakWorld, issue_id: str) -> None:
    """Archive and unarchive an issue while exercising the history search sink."""
    archived: httpx.Response = await world.client.post(
        f"/api/issues/{issue_id}/archive",
        headers=auth(world.admin_token),
        json={"kind": "for_duration", "duration_minutes": 60},
    )
    world.recorder.responses.append(archived.text)
    assert archived.status_code == 200, archived.text
    unarchived: httpx.Response = await world.client.post(
        f"/api/issues/{issue_id}/unarchive", headers=auth(world.admin_token), json={}
    )
    world.recorder.responses.append(unarchived.text)
    assert unarchived.status_code == 200, unarchived.text
    timed: httpx.Response = await world.client.post(
        f"/api/issues/{issue_id}/archive",
        headers=auth(world.admin_token),
        json={"kind": "for_duration", "duration_minutes": 30},
    )
    world.recorder.responses.append(timed.text)
    assert timed.status_code == 200, timed.text
    world.clock.advance(timedelta(minutes=31))
    await world.container.worker.scheduler.tick()
    await world.drain()
    activity: httpx.Response = await world.client.get(
        f"/api/issues/{issue_id}/activity", headers=auth(world.admin_token)
    )
    world.recorder.responses.append(activity.text)
    assert activity.status_code == 200, activity.text


async def _flow_8_organization(world: LeakWorld, member_token: str, sample: Sample) -> None:
    """Exercise integration, ownership, member-edit, and self-token surfaces."""
    integrations: httpx.Response = await world.client.get(
        "/api/integrations", headers=auth(world.admin_token)
    )
    world.recorder.responses.append(integrations.text)
    assert integrations.status_code == 200, integrations.text
    integration_test: httpx.Response = await world.client.post(
        "/api/integrations/email/test", headers=auth(member_token)
    )
    world.recorder.responses.append(integration_test.text)
    assert integration_test.status_code in (200, 503), integration_test.text
    ownership: httpx.Response = await world.client.post(
        "/api/ownership-rules/test",
        headers=auth(world.admin_token),
        json={"source": "custom", "locator": sample.value},
    )
    world.recorder.responses.append(ownership.text)
    assert ownership.status_code == 422, ownership.text
    assert sample.value not in ownership.text
    rejected: httpx.Response = await world.client.patch(
        "/api/members/leak-v13-user",
        headers=auth(world.admin_token),
        json={"display_name": sample.value},
    )
    world.recorder.responses.append(rejected.text)
    assert rejected.status_code == 422, rejected.text
    assert sample.value not in rejected.text
    updated: httpx.Response = await world.client.patch(
        "/api/members/leak-v13-user",
        headers=auth(world.admin_token),
        json={"display_name": "Leak V13 Safe User"},
    )
    world.recorder.responses.append(updated.text)
    assert updated.status_code == 200, updated.text
    token: httpx.Response = await world.client.post(
        "/api/auth/me/tokens",
        headers=auth(member_token),
        json={"label": "safe v13 self token", "scopes": ["read"]},
    )
    world.recorder.responses.append(token.text)
    assert token.status_code == 201, token.text
    assert "token" in token.json()


async def _flow_5_weekly_report(world: LeakWorld, smtp_messages: list[str]) -> None:
    """Drive the weekly report scheduler deterministically through FakeClock."""
    world.clock.set(datetime(2026, 10, 12, 9, 0, tzinfo=UTC))
    queued: int = await world.container.worker.scheduler.tick()
    assert queued >= 1
    await world.drain()
    assert any("weekly report" in message.lower() for message in smtp_messages)


async def _assert_flow_sinks(
    world: LeakWorld, samples: tuple[Sample, ...], smtp_messages: list[str]
) -> None:
    """Assert all five sink classes after each design flow."""
    sinks: tuple[tuple[str, str], ...] = (
        await database_text(world)
        + logs(world, world.log_buffer.getvalue())
        + search_documents(world)
        + outbound_bodies(world)
        + api_responses(world)
        + tuple((f"smtp:{index}", message) for index, message in enumerate(smtp_messages))
    )
    for sample in samples:
        for label, text in sinks:
            assert find_leaks(text) == (), (sample.name, label)
            assert sample.value not in text, (sample.name, label)


async def _flow_9_invalid_query(world: LeakWorld, sample: Sample) -> None:
    """Ensure parser errors do not echo an invalid raw query value."""
    response: httpx.Response = await world.client.get(
        "/api/issues",
        headers=auth(world.admin_token),
        params={"query": "times_seen:" + sample.value},
    )
    world.recorder.responses.append(response.text)
    assert response.status_code == 422, response.text
    assert sample.value not in response.text


async def _member_token(world: LeakWorld) -> str:
    """Create an admin test principal bound to a member for personal-view routes."""
    created: httpx.Response = await world.client.post(
        "/api/members",
        headers=auth(world.admin_token),
        json={
            "handle": "leak-v13-user",
            "display_name": "Leak V13 User",
            "email": "leak-v13-user@example.invalid",
        },
    )
    world.recorder.responses.append(created.text)
    assert created.status_code == 201, created.text
    role: httpx.Response = await world.client.put(
        "/api/users/leak-v13-user/role",
        headers=auth(world.admin_token),
        json={"role": "admin"},
    )
    world.recorder.responses.append(role.text)
    assert role.status_code == 200, role.text
    async with world.database.sessions.begin() as session:
        member: MemberRow | None = await session.scalar(
            select(MemberRow).where(MemberRow.handle == "leak-v13-user")
        )
        assert member is not None
        token: CreatedToken = await TokenService(world.container.audit, world.clock).create(
            session,
            "leak-v13-member",
            frozenset((ApiScope.READ, ApiScope.ADMIN)),
            member.id,
        )
    return token.token
