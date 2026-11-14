"""API acceptance tests for the v1.3 issue stream and saved views."""

from dataclasses import dataclass

import pytest
from _pytest.logging import LogCaptureFixture
from fastapi import APIRouter, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient, Headers
from httpx import Response as HttpxResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telescreen.api.common import ApiContext, PermissionDependency, install_error_handlers
from telescreen.api.routers.issue_counts import IssueCountsRouterDeps, build_issue_counts_router
from telescreen.api.routers.issue_views import IssueViewsRouterDeps, build_issue_views_router
from telescreen.api.routers.issues import IssuesRouterDeps, build_issues_router
from telescreen.core.json import JsonKind, JsonValue, dump_json, parse_json
from telescreen.core.permissions import Permission
from telescreen.core.vocabulary import (
    ActorDisplayKind,
    ApiScope,
    IssueStatus,
    Role,
    RuleClass,
    SavedViewVisibility,
)
from telescreen.db.models import IssueRow, MemberRow, SavedViewRow
from telescreen.directory import CreatedToken, TokenService
from telescreen.issues.views import SavedViewService
from telescreen.ports.directory import ActorDisplay
from telescreen.ports.findings import MaskedFinding
from tests.issues.test_issues import (
    IssueHarness,
    RecordingIndexer,
    make_issue_harness,
    masked_finding,
    resource_scan,
)
from tests.support.samples import Sample, github_token


@dataclass
class FakeActors:
    """Resolve test assignment refs to a stable display name."""

    async def display(
        self, session: AsyncSession, refs: tuple[str, ...]
    ) -> tuple[ActorDisplay, ...]:
        """Return safe display DTOs without exposing raw refs as names."""
        del session
        return tuple(
            ActorDisplay(ref, ActorDisplayKind.MEMBER, "Alice Example", "alice", True)
            for ref in refs
        )


@pytest.mark.asyncio
async def test_issue_stream_counts_cursors_views_and_safe_errors(
    caplog: LogCaptureFixture,
) -> None:
    """Exercise p04 list, counts, saved views, keysets, masking and route permissions."""
    harness: IssueHarness = await make_issue_harness()
    try:
        findings: tuple[MaskedFinding, ...] = (
            masked_finding(harness, RuleClass.TOKEN, "token-one"),
            masked_finding(harness, RuleClass.EMAIL, "email-one"),
            masked_finding(harness, RuleClass.PHONE, "phone-one"),
            masked_finding(harness, RuleClass.CREDENTIAL, "credential-one"),
        )
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(session, resource_scan(harness, findings, "v13-api"))
        async with harness.db.sessions.begin() as session:
            member: MemberRow = MemberRow(
                id="member-v13",
                handle="alice",
                display_name="Alice Example",
                email=None,
                slack_user_id=None,
                active=True,
                role=Role.VIEWER.value,
                created_at=harness.clock.now(),
                updated_at=harness.clock.now(),
            )
            session.add(member)
            issues: list[IssueRow] = list((await session.scalars(select(IssueRow))).all())
            regressed: IssueRow = next(
                issue for issue in issues if issue.rule_class == RuleClass.TOKEN.value
            )
            regressed.status = IssueStatus.REGRESSED.value
            assigned: IssueRow = next(
                issue for issue in issues if issue.rule_class == RuleClass.EMAIL.value
            )
            assigned.assignee_member_id = member.id
            primary: IssueRow = assigned
            child: IssueRow = next(
                issue for issue in issues if issue.id != primary.id and issue.id != regressed.id
            )
            child.merged_into_id = primary.id
            token_service: TokenService = TokenService(harness.audit, harness.clock)
            token: CreatedToken = await token_service.create(
                session, "v13-stream", frozenset((ApiScope.READ,)), member.id
            )
        ctx: ApiContext = ApiContext(harness.db.sessions, harness.clock, harness.audit)
        views: SavedViewService = SavedViewService(harness.redactor, harness.audit, harness.clock)
        app: FastAPI = FastAPI()
        install_error_handlers(app)
        counts_router: APIRouter = build_issue_counts_router(
            IssueCountsRouterDeps(ctx, harness.service.resolver, None)
        )
        app.include_router(counts_router)
        app.include_router(
            build_issues_router(
                IssuesRouterDeps(
                    ctx,
                    harness.service,
                    RecordingIndexer([], []),
                    actors=FakeActors(),
                )
            )
        )
        views_router: APIRouter = build_issue_views_router(IssueViewsRouterDeps(ctx, views))
        app.include_router(views_router)
        checked_routes: int = 0
        actual_permissions: set[tuple[str, str, Permission]] = set()
        expected_permissions: set[tuple[str, str, Permission]] = {
            ("GET", "/api/issues/counts", Permission.ISSUES_READ),
            ("GET", "/api/views", Permission.VIEWS_READ),
            ("POST", "/api/views", Permission.VIEWS_WRITE),
            ("PATCH", "/api/views/{view_id}", Permission.VIEWS_WRITE),
            ("DELETE", "/api/views/{view_id}", Permission.VIEWS_WRITE),
            ("PUT", "/api/views/{view_id}/star", Permission.VIEWS_WRITE),
            ("DELETE", "/api/views/{view_id}/star", Permission.VIEWS_WRITE),
            ("PUT", "/api/views/default", Permission.VIEWS_WRITE),
        }
        for route in counts_router.routes + views_router.routes:
            if not isinstance(route, APIRoute):
                continue
            markers: tuple[PermissionDependency, ...] = _permission_markers(route.dependant)
            assert len(markers) == 1, route.path
            for method in route.methods or set():
                checked_routes += 1
                actual_permissions.add((method, route.path, markers[0].permission))
        assert checked_routes == 8
        assert actual_permissions == expected_permissions
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers: Headers = Headers({"Authorization": f"Bearer {token.token}"})
            unassigned: HttpxResponse = await client.get(
                "/api/issues?assignee=none", headers=headers
            )
            assert unassigned.status_code == 200
            unassigned_ids: set[str] = _ids(_items(unassigned))
            assert assigned.id not in unassigned_ids
            assert child.id not in unassigned_ids
            assigned_response: HttpxResponse = await client.get(
                "/api/issues?query=is%3Aunresolved", headers=headers
            )
            assigned_item: JsonValue = next(
                item for item in _items(assigned_response) if _item_field(item, "id") == assigned.id
            )
            assignee_json: JsonValue | None = assigned_item.get("assignee")
            assert assignee_json is not None
            assert _item_field(assignee_json, "display_name") == "Alice Example"
            trend_json: JsonValue | None = assigned_item.get("trend")
            assert trend_json is not None and trend_json.kind is JsonKind.ARRAY
            assert len(trend_json.items) == 14

            unresolved: HttpxResponse = await client.get(
                "/api/issues?query=is%3Aunresolved", headers=headers
            )
            assert unresolved.status_code == 200
            assert regressed.id in _ids(_items(unresolved))
            negated: HttpxResponse = await client.get(
                "/api/issues?query=%21source%3Aslack", headers=headers
            )
            assert negated.status_code == 200
            assert len(_items(negated)) == 3

            invalid: HttpxResponse = await client.get(
                "/api/issues?query=foo%3Abar", headers=headers
            )
            assert invalid.status_code == 422
            assert b"foo:bar" not in invalid.content
            invalid_payload: JsonValue = parse_json(invalid.content)
            error: JsonValue | None = invalid_payload.get("error")
            assert error is not None and error.get("reason") is not None
            malformed_cursor: HttpxResponse = await client.get(
                "/api/issues?cursor=malformed", headers=headers
            )
            assert malformed_cursor.status_code == 422
            overlong_period: HttpxResponse = await client.get(
                "/api/issues?period=custom&start=2025-01-01T00%3A00%3A00Z&end=2026-10-12T00%3A00%3A00Z",
                headers=headers,
            )
            assert overlong_period.status_code == 422
            sample: Sample = github_token()
            secret_query: HttpxResponse = await client.get(
                "/api/issues", params={"query": f"unknown:{sample.value}"}, headers=headers
            )
            assert secret_query.status_code == 422
            assert sample.value.encode() not in secret_query.content
            assert sample.value not in caplog.text

            for sort in ("priority", "trending"):
                found: list[str] = []
                cursor: str | None = None
                for _ in range(3):
                    path: str = f"/api/issues?sort={sort}&limit=1"
                    if cursor is not None:
                        path += f"&cursor={cursor}"
                    page: HttpxResponse = await client.get(path, headers=headers)
                    assert page.status_code == 200
                    items: list[JsonValue] = _items(page)
                    assert len(items) == 1
                    found.extend(_ids_from_values(items))
                    cursor_value: JsonValue | None = parse_json(page.content).get("next_cursor")
                    cursor = (
                        cursor_value.as_str()
                        if cursor_value is not None and cursor_value.as_str() is not None
                        else None
                    )
                    if cursor is None:
                        break
                assert len(found) == len(set(found)) == 3

            counts: HttpxResponse = await client.get("/api/issues/counts", headers=headers)
            assert counts.status_code == 200
            count_json: JsonValue = parse_json(counts.content)
            tabs: JsonValue | None = count_json.get("tabs")
            assert tabs is not None and tabs.kind is JsonKind.ARRAY
            assert all(_item_field(tab, "tab") != "" for tab in tabs.items)
            unresolved_count: int = next(
                _int_field(tab, "count")
                for tab in tabs.items
                if _item_field(tab, "tab") == "unresolved"
            )
            assert unresolved_count == 3

            create_view: HttpxResponse = await client.post(
                "/api/views",
                headers=headers,
                json={
                    "name": "Open issues",
                    "query": "is:unresolved",
                    "sort": "priority",
                    "period": "14d",
                    "visibility": SavedViewVisibility.PERSONAL.value,
                },
            )
            assert create_view.status_code == 201
            view_json: JsonValue = parse_json(create_view.content)
            view_id_value: JsonValue | None = view_json.get("id")
            assert view_id_value is not None and view_id_value.as_str() is not None
            view_id: str = view_id_value.as_str() or ""
            assert (
                await client.put(f"/api/views/{view_id}/star", headers=headers)
            ).status_code == 200
            assert (
                await client.put("/api/views/default", headers=headers, json={"view_id": view_id})
            ).status_code == 200
            listed: HttpxResponse = await client.get("/api/views", headers=headers)
            assert listed.status_code == 200
            assert b'"starred":true' in listed.content and b'"is_default":true' in listed.content

            rejected: HttpxResponse = await client.post(
                "/api/views",
                headers=headers,
                json={
                    "name": sample.value,
                    "query": "",
                    "sort": "last_seen",
                    "period": "all",
                    "visibility": SavedViewVisibility.PERSONAL.value,
                },
            )
            assert rejected.status_code == 422
            assert sample.value.encode() not in rejected.content
            shared: HttpxResponse = await client.post(
                "/api/views",
                headers=headers,
                json={
                    "name": "Shared view",
                    "query": "",
                    "sort": "last_seen",
                    "period": "all",
                    "visibility": SavedViewVisibility.SHARED.value,
                },
            )
            assert shared.status_code == 403
            async with harness.db.sessions.begin() as session:
                for index in range(49):
                    session.add(
                        SavedViewRow(
                            id=f"limit-view-{index}",
                            name=f"View {index}",
                            query="",
                            sort="last_seen",
                            period="all",
                            visibility=SavedViewVisibility.PERSONAL.value,
                            owner_member_id="member-v13",
                            position=index + 1,
                            created_at=harness.clock.now(),
                            updated_at=harness.clock.now(),
                        )
                    )
            limit_response: HttpxResponse = await client.post(
                "/api/views",
                headers=headers,
                json={
                    "name": "51st view",
                    "query": "",
                    "sort": "last_seen",
                    "period": "all",
                    "visibility": SavedViewVisibility.PERSONAL.value,
                },
            )
            assert limit_response.status_code == 422
        async with harness.db.sessions() as session:
            saved_count: int = len(list((await session.scalars(select(SavedViewRow))).all()))
        assert saved_count == 50
        assert any(entry.action == "view.rejected" for entry in harness.audit.entries)
        rejected_audit: str = next(
            dump_json(entry.details)
            for entry in harness.audit.entries
            if entry.action == "view.rejected"
        )
        assert sample.value not in rejected_audit
    finally:
        await harness.close()


def _items(response: HttpxResponse) -> list[JsonValue]:
    """Return an API item's JSON array after asserting its shape."""
    payload: JsonValue = parse_json(response.content)
    items: JsonValue | None = payload.get("items")
    assert items is not None and items.kind is JsonKind.ARRAY
    return list(items.items)


def _permission_markers(dependant: Dependant) -> tuple[PermissionDependency, ...]:
    """Collect route permission declarations recursively from FastAPI dependencies."""
    markers: list[PermissionDependency] = []
    for dependency in dependant.dependencies:
        if isinstance(dependency.call, PermissionDependency):
            markers.append(dependency.call)
        markers.extend(_permission_markers(dependency))
    return tuple(markers)


def _ids(items: list[JsonValue]) -> set[str]:
    """Extract non-empty item IDs."""
    return set(_ids_from_values(items))


def _ids_from_values(items: list[JsonValue]) -> list[str]:
    """Extract item IDs in response order."""
    values: list[str] = []
    for item in items:
        value: JsonValue | None = item.get("id")
        assert value is not None and value.as_str() is not None
        values.append(value.as_str() or "")
    return values


def _item_field(item: JsonValue, name: str) -> str:
    """Return one string field or an empty fallback for safe shape assertions."""
    value: JsonValue | None = item.get(name)
    return value.as_str() or "" if value is not None else ""


def _int_field(item: JsonValue, name: str) -> int:
    """Return one integer field or zero for safe count assertions."""
    value: JsonValue | None = item.get(name)
    return value.as_int() or 0 if value is not None else 0
