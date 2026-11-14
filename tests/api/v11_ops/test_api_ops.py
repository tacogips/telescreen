"""API regressions for v1.1 operational endpoints."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient, Headers, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.app import create_app
from telescreen.api.common import ApiContext, install_error_handlers
from telescreen.api.routers.collectors import (
    CollectorHealthLister,
    CollectorsRouterDeps,
    WebhookSecrets,
    build_collectors_router,
)
from telescreen.api.routers.directory import DirectoryRouterDeps, build_directory_router
from telescreen.api.routers.human_tasks import HumanTasksRouterDeps, build_human_tasks_router
from telescreen.collectors.coverage import CoverageRecorder
from telescreen.collectors.health import CollectorHealthView, list_health
from telescreen.collectors.registry import CollectorRegistry
from telescreen.collectors.repository import CollectorRepository, ScanJobStoreImpl
from telescreen.collectors.service import CollectorService
from telescreen.core.clock import FakeClock
from telescreen.core.config import TelescreenSettings
from telescreen.core.ids import new_id
from telescreen.core.json import dump_json
from telescreen.core.secrets import EnvSource
from telescreen.core.vocabulary import (
    ApiScope,
    CollectorKind,
    CoverageState,
    ExceptionDuration,
    HumanTaskKind,
    IdentityKind,
    RemediationAction,
    ScanJobStatus,
    ScanTrigger,
    SourceFormat,
    TriggerMode,
    Visibility,
)
from telescreen.db.models import (
    CollectorConfigRow,
    HumanTaskRow,
    MemberIdentityRow,
    MemberRow,
    PoliceWorkerRow,
    ResourceCoverageRow,
    ScanCoverageRow,
    ScanJobRow,
    StepRunRow,
    WorkflowDefinitionRow,
    WorkflowRunRow,
)
from telescreen.directory.identities import IdentityService
from telescreen.directory.resolver import DirectoryRecipientResolver
from telescreen.directory.service import DirectoryService
from telescreen.directory.tokens import CreatedToken, TokenService
from telescreen.evidence.query import EvidenceQueryImpl
from telescreen.masking.fingerprint import Fingerprinter
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry
from telescreen.ports.collectors import CollectorSecrets, GitHubSelector
from telescreen.ports.directory import OwnerDirectory
from telescreen.ports.issues import RemediationReceipt, ResourceIssueRef
from telescreen.ports.sweeps import SweepLauncher
from telescreen.ports.workflow import StepSignal
from telescreen.wiring import Container, build_container, close_container
from telescreen.workflow.dsl.compiled import compiled_to_json
from telescreen.workflow.dsl.loader import CompiledWorkflow, load_workflow
from telescreen.workflow.engine.repository import WorkflowRepository
from telescreen.workflow.queue.sql_queue import SqlTaskQueue
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import github_token

_NOW: datetime = datetime(2026, 10, 10, 0, 0, tzinfo=UTC)


@dataclass(frozen=True)
class _TestEnv(EnvSource):
    """Supply the fingerprint key required by the composition root."""

    secret: str

    def get(self, key: str, /) -> str | None:
        """Resolve the test-only configured secret."""
        return self.secret if key == "TELESCREEN_TEST_KEY" else None


@dataclass
class RecordingAudit:
    """Capture audit events without writing secondary rows."""

    entries: list[AuditEntry]

    async def append(self, session: AsyncSession, entry: AuditEntry) -> int:
        """Record an audit entry."""
        del session
        self.entries.append(entry)
        return len(self.entries)


@dataclass(frozen=True)
class NoopLauncher(SweepLauncher):
    """Provide a sweep launcher for routes not exercised by these tests."""

    async def start_collector_sweep(self, collector_config_id: str, trigger: ScanTrigger) -> str:
        """Return a deterministic placeholder job id."""
        del collector_config_id, trigger
        return "map-job"


@dataclass(frozen=True)
class HealthLister(CollectorHealthLister):
    """Use the production collector health query for router tests."""

    async def list(
        self,
        session: AsyncSession,
        configs: tuple[CollectorConfigRow, ...],
        now: datetime,
    ) -> tuple[tuple[str, CollectorHealthView], ...]:
        """Return calculated collector health views."""
        return await list_health(session, configs, now)


@dataclass(frozen=True)
class IssueRefs:
    """Return one safe issue reference for every requested test resource."""

    async def open_issues_for_resources(
        self, session: AsyncSession, resource_keys: tuple[str, ...]
    ) -> tuple[ResourceIssueRef, ...]:
        """Return the issue canary associated with the resource key."""
        del session
        return tuple(ResourceIssueRef(key, "issue-1", "TELE-1") for key in resource_keys)


@dataclass(frozen=True)
class Owners(OwnerDirectory):
    """Map one test member to handle-keyed member and team refs."""

    async def member_ref(self, session: AsyncSession, member_id: str) -> str | None:
        """Return the canonical handle reference."""
        del session, member_id
        return "member:alice"

    async def team_ref(self, session: AsyncSession, team_id: str) -> str | None:
        """Return no standalone team reference."""
        del session, team_id
        return None

    async def team_refs_of(self, session: AsyncSession, member_id: str) -> tuple[str, ...]:
        """Return the test member's team reference."""
        del session, member_id
        return ("team:responders",)

    async def member_for_owner(self, session: AsyncSession, owner_ref: str) -> str | None:
        """Return no identity lookup result."""
        del session, owner_ref
        return None

    async def team_lead(self, session: AsyncSession, team_slug: str) -> str | None:
        """Return no lead reference."""
        del session, team_slug
        return None


@dataclass
class Signaler:
    """Collect workflow signals for task API construction."""

    signals: list[tuple[str, StepSignal]]

    async def signal(self, session: AsyncSession, step_run_id: str, signal: StepSignal) -> None:
        """Record the signal."""
        del session
        self.signals.append((step_run_id, signal))


@dataclass
class Remediation:
    """Record issue actions and optionally fail verification for rollback checks."""

    calls: list[str]
    fail_verification: bool = False

    async def request_verification(
        self,
        session: AsyncSession,
        issue_id: str,
        action: RemediationAction,
        include_linked: bool,
        actor_ref: str,
        note: str | None,
    ) -> RemediationReceipt:
        """Record the requested verification action."""
        del session, include_linked, actor_ref, note
        self.calls.append(f"verify:{issue_id}:{action.value}")
        if self.fail_verification:
            raise RuntimeError("test remediation failure")
        return RemediationReceipt((issue_id,), 1)

    async def request_exception(
        self,
        session: AsyncSession,
        issue_id: str,
        reason: str,
        duration: ExceptionDuration,
        actor_ref: str,
        is_admin: bool,
    ) -> str:
        """Record the requested time-bounded exception."""
        del session, reason, actor_ref, is_admin
        self.calls.append(f"exception:{issue_id}:{duration.value}")
        return "exception-1"

    async def release_to_team(self, session: AsyncSession, issue_id: str, actor_ref: str) -> None:
        """Record reassignment to the issue's owner team."""
        del session, actor_ref
        self.calls.append(f"release:{issue_id}")


async def _token(
    harness: SqliteHarness,
    audit: RecordingAudit,
    clock: FakeClock,
    label: str,
    scopes: frozenset[ApiScope],
    member_id: str | None,
) -> CreatedToken:
    """Create an API token for an in-process router test."""
    async with harness.sessions.begin() as session:
        return await TokenService(audit, clock).create(session, label, scopes, member_id)


def _app(ctx: ApiContext, routers: tuple[APIRouter, ...]) -> FastAPI:
    """Build an in-process API app with shared error handling."""
    app: FastAPI = FastAPI()
    install_error_handlers(app)
    for router in routers:
        app.include_router(router)
    return app


@pytest.mark.asyncio
async def test_collector_health_coverage_privacy_and_directory_identities() -> None:
    """Expose health and coverage while keeping locators and identity values private."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(_NOW)
    audit: RecordingAudit = RecordingAudit([])
    ctx: ApiContext = ApiContext(harness.sessions, clock, audit)
    repository: CollectorRepository = CollectorRepository()
    fingerprinter: Fingerprinter = Fingerprinter(b"k" * 32, "test-key")
    config_id: str = new_id()
    resource_key: str = "abcdef123456" + "0" * 52
    sample: str = github_token().value
    async with harness.sessions.begin() as session:
        await repository.create(
            session,
            config_id,
            "github-main",
            CollectorKind.GITHUB,
            TriggerMode.SCHEDULE,
            "0 * * * *",
            None,
            16,
            GitHubSelector("example", ("repo",), (), ()),
            None,
            clock.now(),
        )
        session.add(
            ScanJobRow(
                id="scan-1",
                collector_config_id=config_id,
                source="github",
                trigger=ScanTrigger.SCHEDULE.value,
                status=ScanJobStatus.PARTIAL.value,
                started_at=clock.now() - timedelta(days=1),
                finished_at=clock.now(),
                resources_scanned=0,
                resources_skipped=1,
                findings_count=0,
                error_code=None,
            )
        )
        session.add(
            ScanCoverageRow(
                scan_job_id="scan-1",
                state=CoverageState.PERMISSION_DENIED.value,
                reason="",
                count=1,
            )
        )
        session.add(
            ResourceCoverageRow(
                id="coverage-1",
                collector_config_id=config_id,
                resource_key=resource_key,
                source="github",
                state=CoverageState.PERMISSION_DENIED.value,
                reason=None,
                truncated_bytes=0,
                exposure_visibility=Visibility.PUBLIC.value,
                first_seen_at=clock.now() - timedelta(days=2),
                last_attempt_at=clock.now(),
            )
        )
    service: CollectorService = CollectorService(
        CollectorRegistry(),
        SqlTaskQueue(clock),
        ScanJobStoreImpl(repository),
        repository,
        fingerprinter,
        Redactor.default(),
        audit,
        clock,
    )
    directory: DirectoryService = DirectoryService(audit, clock, fingerprinter=fingerprinter)
    identities: IdentityService = IdentityService(fingerprinter, audit, clock)
    admin: CreatedToken = await _token(
        harness, audit, clock, "admin", frozenset((ApiScope.ADMIN,)), None
    )
    read: CreatedToken = await _token(
        harness, audit, clock, "read", frozenset((ApiScope.READ,)), None
    )
    app: FastAPI = _app(
        ctx,
        (
            build_collectors_router(
                CollectorsRouterDeps(
                    ctx,
                    service,
                    NoopLauncher(),
                    WebhookSecrets(
                        CollectorSecrets(None, None),
                        CollectorSecrets(None, None),
                        CollectorSecrets(None, None),
                    ),
                    CoverageRecorder(),
                    IssueRefs(),
                    HealthLister(),
                )
            ),
            build_directory_router(DirectoryRouterDeps(ctx, directory, identities)),
        ),
    )
    try:
        transport: ASGITransport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers: Headers = Headers({"Authorization": f"Bearer {read.token}"})
            collectors: Response = await client.get("/api/collectors", headers=headers)
            assert collectors.status_code == 200, collectors.text
            assert collectors.json()["items"][0]["health"]["status"] == "failing"
            summary: Response = await client.get(
                f"/api/collectors/{config_id}/coverage", headers=headers
            )
            assert summary.status_code == 200
            assert summary.json()["counts"][0]["count"] == 1
            assert summary.json()["unscannable_total"] == 1
            resources: Response = await client.get(
                f"/api/collectors/{config_id}/coverage/resources", headers=headers
            )
            assert resources.status_code == 200
            assert resources.json()["items"][0]["resource_id"] == resource_key[:12]
            assert resources.json()["items"][0]["issues"] == [
                {"issue_id": "issue-1", "short_id": "TELE-1"}
            ]
            assert resource_key not in resources.text
            assert "docs/canary-v11.txt" not in resources.text
            missing: Response = await client.get(
                "/api/collectors/missing/coverage", headers=headers
            )
            assert missing.status_code == 404

            admin_headers: Headers = Headers({"Authorization": f"Bearer {admin.token}"})
            member: Response = await client.post(
                "/api/members",
                headers=admin_headers,
                json={"handle": "alice", "display_name": "Alice"},
            )
            assert member.status_code == 201
            team: Response = await client.post(
                "/api/teams",
                headers=admin_headers,
                json={"slug": "responders", "name": "Responders", "lead": "alice"},
            )
            assert team.status_code == 201
            listed_teams: Response = await client.get("/api/teams", headers=headers)
            assert listed_teams.json()["items"][0]["lead"] == "alice"
            changed_lead: Response = await client.put(
                "/api/teams/responders/lead",
                headers=admin_headers,
                json={"lead": None},
            )
            assert changed_lead.status_code == 204
            assert (await client.get("/api/teams", headers=headers)).json()["items"][0][
                "lead"
            ] is None

            identity: Response = await client.post(
                "/api/members/alice/identities",
                headers=admin_headers,
                json={"kind": IdentityKind.GITHUB_LOGIN.value, "value": sample},
            )
            assert identity.status_code == 201
            assert sample not in identity.text
            assert set(identity.json()) == {"id", "kind", "origin", "key_prefix", "created_at"}
            identity_id: str = identity.json()["id"]
            identity_rows: Response = await client.get(
                "/api/members/alice/identities", headers=headers
            )
            assert identity_rows.status_code == 200
            assert sample not in identity_rows.text
            async with harness.sessions() as session:
                stored: MemberIdentityRow | None = await session.scalar(
                    select(MemberIdentityRow).where(MemberIdentityRow.kind == "github_login")
                )
                assert stored is not None
                assert sample not in stored.identity_key
            forbidden: Response = await client.post(
                "/api/members/alice/identities",
                headers=headers,
                json={"kind": IdentityKind.GITHUB_LOGIN.value, "value": "member-login"},
            )
            assert forbidden.status_code == 403
            derived: Response = await client.post(
                "/api/members/alice/identities",
                headers=admin_headers,
                json={"kind": IdentityKind.SLACK_USER.value, "value": "slack-derived"},
            )
            assert derived.status_code == 422
            removed: Response = await client.delete(
                f"/api/members/alice/identities/{identity_id}", headers=admin_headers
            )
            assert removed.status_code == 204
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_assignee_me_includes_handle_and_team_refs_and_memberless_is_forbidden() -> None:
    """Filter My tasks by the canonical member handle and all team refs."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(_NOW)
    audit: RecordingAudit = RecordingAudit([])
    ctx: ApiContext = ApiContext(harness.sessions, clock, audit)
    directory: DirectoryService = DirectoryService(audit, clock)
    async with harness.sessions.begin() as session:
        member: MemberRow = await directory.create_member(session, "alice", "Alice", None, None)
        member_id: str = member.id
        member.role = "viewer"
        session.add_all(
            [
                HumanTaskRow(
                    id="task-member",
                    run_id="run-member",
                    step_run_id="step-member",
                    kind="owner_review",
                    title="Owner review",
                    assignee_ref="member:alice",
                    status="open",
                ),
                HumanTaskRow(
                    id="task-team",
                    run_id="run-team",
                    step_run_id="step-team",
                    kind="owner_review",
                    title="Owner review",
                    assignee_ref="team:responders",
                    status="open",
                ),
                HumanTaskRow(
                    id="task-other",
                    run_id="run-other",
                    step_run_id="step-other",
                    kind="approval",
                    title="Approval",
                    assignee_ref="member:bob",
                    status="open",
                ),
            ]
        )
    member_token: CreatedToken = await _token(
        harness, audit, clock, "member", frozenset((ApiScope.READ,)), member_id
    )
    no_member_token: CreatedToken = await _token(
        harness, audit, clock, "no-member", frozenset((ApiScope.READ,)), None
    )
    app: FastAPI = _app(
        ctx,
        (
            build_human_tasks_router(
                HumanTasksRouterDeps(
                    ctx,
                    DirectoryRecipientResolver(),
                    EvidenceQueryImpl(),
                    Signaler([]),
                    WorkflowRepository(),
                    Redactor.default(),
                    Owners(),
                )
            ),
        ),
    )
    try:
        transport: ASGITransport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            own: Response = await client.get(
                "/api/human-tasks?assignee=me",
                headers={"Authorization": f"Bearer {member_token.token}"},
            )
            assert own.status_code == 200
            assert {item["id"] for item in own.json()["items"]} == {"task-member", "task-team"}
            assert all(item["kind"] == "owner_review" for item in own.json()["items"])
            no_member_implicit: Response = await client.get(
                "/api/human-tasks", headers={"Authorization": f"Bearer {no_member_token.token}"}
            )
            no_member_me: Response = await client.get(
                "/api/human-tasks?assignee=me",
                headers={"Authorization": f"Bearer {no_member_token.token}"},
            )
            assert no_member_implicit.status_code == 403
            assert no_member_me.status_code == 403
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_owner_review_actions_validate_and_rollback_atomically() -> None:
    """Apply owner-review effects before signaling and preserve open state on failure."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(_NOW)
    audit: RecordingAudit = RecordingAudit([])
    ctx: ApiContext = ApiContext(harness.sessions, clock, audit)
    definition_id: str = "owner-review-definition"
    workflow_source: str = """apiVersion: telescreen.workflow/v1
kind: Workflow
name: owner-review-api
start: review
steps:
  - id: review
    type: human_task
    kind: owner_review
    title: Review issue
    assignee: issue:assignee
    outcomes: {exception: done, not_mine: done, remove: done, restrict: done}
  - id: done
    type: end
    result: succeeded
"""
    workflow: CompiledWorkflow = load_workflow(workflow_source, SourceFormat.YAML)
    compiled_json: str = dump_json(compiled_to_json(workflow))
    task_specs: tuple[tuple[str, str, str, str], ...] = (
        ("task-remove", "run-remove", "step-remove", "issue-remove"),
        ("task-exception", "run-exception", "step-exception", "issue-exception"),
        ("task-not-mine", "run-not-mine", "step-not-mine", "issue-not-mine"),
        ("task-failure", "run-failure", "step-failure", "issue-failure"),
    )
    async with harness.sessions.begin() as session:
        session.add(
            WorkflowDefinitionRow(
                id=definition_id,
                name="owner-review-api",
                version=1,
                source_format=SourceFormat.YAML.value,
                source_text=workflow_source,
                compiled_json=compiled_json,
                active=True,
                created_by="test",
                created_at=clock.now(),
            )
        )
        for task_id, run_id, step_id, issue_id in task_specs:
            session.add(
                WorkflowRunRow(
                    id=run_id,
                    definition_id=definition_id,
                    issue_id=issue_id,
                    status="waiting",
                    trigger="manual",
                    context_json="{}",
                    lock_version=0,
                    started_at=clock.now(),
                    finished_at=None,
                )
            )
            session.add(
                StepRunRow(
                    id=step_id,
                    run_id=run_id,
                    step_id="review",
                    visit=1,
                    branch_token=None,
                    status="waiting",
                    attempt=1,
                    input_json="{}",
                    output_json=None,
                    outcome=None,
                    error_code=None,
                    started_at=clock.now(),
                    finished_at=None,
                )
            )
            session.add(
                HumanTaskRow(
                    id=task_id,
                    run_id=run_id,
                    step_run_id=step_id,
                    kind=HumanTaskKind.OWNER_REVIEW.value,
                    title="Review issue",
                    assignee_ref="member:alice",
                    status="open",
                )
            )
    admin: CreatedToken = await _token(
        harness, audit, clock, "admin", frozenset((ApiScope.ADMIN,)), None
    )
    remediation: Remediation = Remediation([])
    signaler: Signaler = Signaler([])
    app: FastAPI = _app(
        ctx,
        (
            build_human_tasks_router(
                HumanTasksRouterDeps(
                    ctx,
                    DirectoryRecipientResolver(),
                    EvidenceQueryImpl(),
                    signaler,
                    WorkflowRepository(),
                    Redactor.default(),
                    remediation=remediation,
                )
            ),
        ),
    )
    try:
        transport: ASGITransport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers: Headers = Headers({"Authorization": f"Bearer {admin.token}"})
            remove: Response = await client.post(
                "/api/human-tasks/task-remove/complete",
                headers=headers,
                json={"outcome": "remove"},
            )
            assert remove.status_code == 200
            missing_comment: Response = await client.post(
                "/api/human-tasks/task-exception/complete",
                headers=headers,
                json={"outcome": "exception", "exception_duration": "30d"},
            )
            assert missing_comment.status_code == 422
            exception: Response = await client.post(
                "/api/human-tasks/task-exception/complete",
                headers=headers,
                json={
                    "outcome": "exception",
                    "comment": "Reviewed for limited retention",
                    "exception_duration": "30d",
                },
            )
            assert exception.status_code == 200
            not_mine: Response = await client.post(
                "/api/human-tasks/task-not-mine/complete",
                headers=headers,
                json={"outcome": "not_mine"},
            )
            assert not_mine.status_code == 200
            remediation.fail_verification = True
            failed: Response = await client.post(
                "/api/human-tasks/task-failure/complete",
                headers=headers,
                json={"outcome": "restrict"},
            )
            assert failed.status_code == 500
        assert remediation.calls == [
            "verify:issue-remove:remove",
            "exception:issue-exception:30d",
            "release:issue-not-mine",
            "verify:issue-failure:restrict",
        ]
        assert len(signaler.signals) == 3
        async with harness.sessions() as session:
            failed_task: HumanTaskRow | None = await session.get(HumanTaskRow, "task-failure")
            assert failed_task is not None
            assert failed_task.status == "open"
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_police_workers_include_health_status_and_conditions() -> None:
    """Expose calculated worker health on the Police list endpoint."""
    harness: SqliteHarness = await make_sqlite_harness()
    env: _TestEnv = _TestEnv("t" * 32)
    settings: TelescreenSettings = TelescreenSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        fingerprint_key_ref="env:TELESCREEN_TEST_KEY",
    )
    outbound: httpx.AsyncClient = httpx.AsyncClient()
    container: Container = build_container(settings, env, outbound, harness.sessions)
    try:
        async with harness.sessions.begin() as session:
            session.add(
                PoliceWorkerRow(
                    id="worker-1",
                    name="worker",
                    version="1.0",
                    mode="pull",
                    contract_version="1.0",
                    task_types="",
                    labels="",
                    concurrency=1,
                    endpoint_url=None,
                    token_hash=None,
                    status="active",
                    last_heartbeat_at=None,
                    last_delivery_at=None,
                    created_at=_NOW,
                    health_alerted_at=None,
                )
            )
            token: CreatedToken = await container.token_service.create(
                session, "police-health", frozenset((ApiScope.ADMIN,)), None
            )
        app: FastAPI = create_app(container)
        transport: ASGITransport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response: Response = await client.get(
                "/api/police/workers",
                headers={"Authorization": f"Bearer {token.token}"},
            )
        assert response.status_code == 200
        assert response.json()["items"][0]["health"] == {
            "status": "warning",
            "conditions": [],
        }
    finally:
        await close_container(container)
        await harness.close()
        await outbound.aclose()
