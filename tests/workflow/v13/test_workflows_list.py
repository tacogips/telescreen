"""Enriched workflow list response tests for the alerts backend plan."""

from datetime import datetime, timedelta

import httpx
from fastapi import FastAPI

from telescreen.api.common import ApiContext, install_error_handlers
from telescreen.api.common.auth import hash_token
from telescreen.api.routers.workflows import WorkflowsRouterDeps, build_workflows_router
from telescreen.core.json import JsonValue, parse_json
from telescreen.core.vocabulary import ApiScope, RunStatus, RunTrigger, SourceFormat
from telescreen.db.models import ApiTokenRow, WorkflowDefinitionRow, WorkflowRunRow
from tests.notifications.v13.fakes import RecordingAudit
from tests.workflow.engine.test_engine_extended import EngineRig, make_rig


async def test_workflows_list_includes_active_trigger_and_30_day_run_counts() -> None:
    """List items show active trigger data and count runs across the prior 30 days."""
    rig: EngineRig = await make_rig()
    try:
        source: str = """apiVersion: telescreen.workflow/v1
kind: Workflow
name: list-workflow
trigger:
  "on": [issue_created]
start: finish
steps:
  - id: finish
    type: end
    result: succeeded
"""
        async with rig.harness.sessions.begin() as session:
            definition: WorkflowDefinitionRow = await rig.service.upload(
                session, SourceFormat.YAML, source, "member:test"
            )
            definition = await rig.service.activate(session, definition.name, definition.version)
            for index, status in enumerate(
                (RunStatus.RUNNING, RunStatus.WAITING, RunStatus.SUCCEEDED)
            ):
                session.add(_run(f"run-{index}", definition.id, status, rig.clock.now()))
            session.add(
                _run(
                    "run-old",
                    definition.id,
                    RunStatus.FAILED,
                    rig.clock.now() - timedelta(days=31),
                )
            )
            session.add(
                ApiTokenRow(
                    id="workflow-admin",
                    label="workflow admin",
                    token_hash=hash_token("workflow-admin-token"),
                    scopes=ApiScope.ADMIN.value,
                    member_id=None,
                    created_at=rig.clock.now(),
                    revoked_at=None,
                )
            )
        app: FastAPI = FastAPI()
        install_error_handlers(app)
        context: ApiContext = ApiContext(rig.harness.sessions, rig.clock, RecordingAudit())
        app.include_router(
            build_workflows_router(WorkflowsRouterDeps(context, rig.service, rig.engine))
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://telescreen.test"
        ) as client:
            response: httpx.Response = await client.get(
                "/api/workflows",
                headers={"Authorization": "Bearer workflow-admin-token"},
            )
        assert response.status_code == 200
        document: JsonValue = parse_json(response.text)
        items: JsonValue | None = document.get("items")
        assert items is not None and len(items.items) == 1
        item: JsonValue = items.items[0]
        active: JsonValue | None = item.get("active")
        assert active is not None and active.as_bool() is True
        triggers: JsonValue | None = item.get("trigger_events")
        assert triggers is not None and tuple(value.str_value for value in triggers.items) == (
            "issue_created",
        )
        counts: JsonValue | None = item.get("runs_30d")
        assert counts is not None
        count_values: tuple[int | None, ...] = tuple(
            count.as_int() if (count := counts.get(name)) is not None else None
            for name in ("running", "waiting", "succeeded", "failed", "cancelled")
        )
        assert count_values == (1, 1, 1, 0, 0)
        last_run: JsonValue | None = item.get("last_run_at")
        assert last_run is not None and last_run.as_str() == rig.clock.now().isoformat()
    finally:
        await rig.close()


def _run(
    run_id: str, definition_id: str, status: RunStatus, started_at: datetime
) -> WorkflowRunRow:
    """Build one minimal workflow run row."""
    return WorkflowRunRow(
        id=run_id,
        definition_id=definition_id,
        issue_id=None,
        parent_step_run_id=None,
        status=status.value,
        trigger=RunTrigger.MANUAL.value,
        context_json="{}",
        lock_version=0,
        started_at=started_at,
        finished_at=None,
    )
