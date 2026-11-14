"""HTTP regression coverage for v1.1 issue API projections and filters."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Headers
from httpx import Response as HttpxResponse
from sqlalchemy import select

from telescreen.api.common import ApiContext, install_error_handlers
from telescreen.api.routers.exceptions import ExceptionsRouterDeps, build_exceptions_router
from telescreen.api.routers.issues import IssuesRouterDeps, build_issues_router
from telescreen.api.routers.policies import PoliciesRouterDeps, build_policies_router
from telescreen.core.json import JsonKind, JsonValue, parse_json
from telescreen.core.vocabulary import (
    ApiScope,
    IdentityKind,
    IdentityOrigin,
    RuleClass,
    SourceFormat,
    Visibility,
)
from telescreen.db.models import IssueRow, MemberIdentityRow, MemberRow
from telescreen.detection import RuleDetector
from telescreen.directory import CreatedToken, TokenService
from telescreen.directory.owners import DirectoryOwnerResolver
from telescreen.issues.exceptions import ExceptionService
from telescreen.issues.lifecycle import IssueRemediationService
from telescreen.issues.policy.decider import PlacementPolicyDecider
from telescreen.issues.policy.store import PolicyStore
from telescreen.masking.redact import Redactor
from telescreen.ports.directory import RecipientResolver
from telescreen.workflow.queue.sql_queue import SqlTaskQueue
from tests.issues.test_issues import (
    IssueHarness,
    RecordingIndexer,
    make_issue_harness,
    masked_finding,
    resource_scan,
)
from tests.support.samples import assert_no_leaks, github_token


@pytest.mark.asyncio
async def test_issue_v11_risk_exposure_assignee_and_safe_detail() -> None:
    """Expose safe v1.1 fields and stable risk pages through the HTTP router."""
    harness: IssueHarness = await make_issue_harness()
    indexer: RecordingIndexer = RecordingIndexer([], [])
    try:
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session,
                resource_scan(
                    harness,
                    (masked_finding(harness, RuleClass.TOKEN, "one"),),
                    "scan-one",
                    resource_locator="resource:one",
                ),
            )
            await harness.service.record_scan(
                session,
                resource_scan(
                    harness,
                    (masked_finding(harness, RuleClass.TOKEN, "one"),),
                    "scan-linked-token",
                    resource_locator="resource:linked-token",
                ),
            )
            await harness.service.record_scan(
                session,
                resource_scan(
                    harness,
                    (masked_finding(harness, RuleClass.EMAIL, "two"),),
                    "scan-two",
                    resource_locator="resource:two",
                ),
            )
            await harness.service.record_scan(
                session,
                resource_scan(
                    harness,
                    (masked_finding(harness, RuleClass.PHONE, "three"),),
                    "scan-three",
                    resource_locator="resource:three",
                ),
            )
            session.add(
                MemberRow(
                    id="member-api-owner",
                    handle="api-owner",
                    display_name="API Owner",
                    email=None,
                    slack_user_id=None,
                    active=True,
                    created_at=harness.clock.now(),
                    updated_at=harness.clock.now(),
                    role="responder",
                )
            )
            session.add(
                MemberIdentityRow(
                    id="identity-api-owner",
                    member_id="member-api-owner",
                    kind=IdentityKind.EMAIL.value,
                    identity_key="a" * 64,
                    origin=IdentityOrigin.MEMBER_EMAIL.value,
                    created_at=harness.clock.now(),
                )
            )
        async with harness.db.sessions.begin() as session:
            rows: list[IssueRow] = list((await session.scalars(select(IssueRow))).all())
            token_rows: list[IssueRow] = [
                row for row in rows if row.rule_class == RuleClass.TOKEN.value
            ]
            target_issue: IssueRow = token_rows[0]
            target_issue.risk_score = 70
            target_issue.exposure_visibility = Visibility.PUBLIC.value
            target_issue.exposure_json = (
                '{"visibility":"public","external_principal_count":2,'
                '"shared_drive":false,"is_fork":false}'
            )
            target_issue.assignee_member_id = "member-api-owner"
            target_issue.owner_ref = "email:" + "a" * 64
            token_rows[1].risk_score = 60
            token_rows[1].exposure_visibility = Visibility.INTERNAL.value
            email_issue: IssueRow = next(
                row for row in rows if row.rule_class == RuleClass.EMAIL.value
            )
            email_issue.risk_score = 90
            email_issue.exposure_visibility = Visibility.PRIVATE.value
            phone_issue: IssueRow = next(
                row for row in rows if row.rule_class == RuleClass.PHONE.value
            )
            phone_issue.risk_score = 80
            phone_issue.exposure_visibility = Visibility.INTERNAL.value
            target_issue_id: str = target_issue.id
            token: CreatedToken = await TokenService(harness.audit, harness.clock).create(
                session,
                "v11-issues-api",
                frozenset((ApiScope.READ, ApiScope.WRITE)),
                "member-api-owner",
            )
        ctx: ApiContext = ApiContext(harness.db.sessions, harness.clock, harness.audit)
        owners: DirectoryOwnerResolver = DirectoryOwnerResolver()
        app: FastAPI = FastAPI()
        install_error_handlers(app)
        app.include_router(
            build_issues_router(
                IssuesRouterDeps(
                    ctx,
                    harness.service,
                    indexer,
                    owners=owners,
                    resolver=harness.service.resolver,
                )
            )
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers: Headers = Headers({"Authorization": f"Bearer {token.token}"})
            public: HttpxResponse = await client.get("/api/issues?exposure=public", headers=headers)
            assert public.status_code == 200
            public_payload: JsonValue = parse_json(public.content)
            public_items: JsonValue | None = public_payload.get("items")
            assert public_items is not None and len(public_items.items) == 1

            first: HttpxResponse = await client.get(
                "/api/issues?sort=risk&limit=1", headers=headers
            )
            first_payload: JsonValue = parse_json(first.content)
            first_items: JsonValue | None = first_payload.get("items")
            cursor_value: JsonValue | None = first_payload.get("next_cursor")
            assert first.status_code == 200
            assert first_items is not None and len(first_items.items) == 1
            first_score: JsonValue | None = first_items.items[0].get("risk_score")
            assert first_score is not None and first_score.as_int() == 90
            assert cursor_value is not None and cursor_value.as_str() is not None
            second: HttpxResponse = await client.get(
                f"/api/issues?sort=risk&limit=1&cursor={cursor_value.as_str()}",
                headers=headers,
            )
            second_payload: JsonValue = parse_json(second.content)
            second_items: JsonValue | None = second_payload.get("items")
            assert second.status_code == 200
            assert second_items is not None and len(second_items.items) == 1
            second_score: JsonValue | None = second_items.items[0].get("risk_score")
            assert second_score is not None and second_score.as_int() == 80

            occurrences: HttpxResponse = await client.get(
                f"/api/issues/{target_issue_id}/occurrences", headers=headers
            )
            occurrences_payload: JsonValue = parse_json(occurrences.content)
            occurrence_items: JsonValue | None = occurrences_payload.get("items")
            key_versions: JsonValue | None = occurrences_payload.get("key_versions")
            assert occurrences.status_code == 200
            assert occurrence_items is not None and len(occurrence_items.items) == 1
            assert key_versions is not None
            assert tuple(value.as_int() for value in key_versions.items) == (1,)

            mine: HttpxResponse = await client.get("/api/issues?assignee=me", headers=headers)
            mine_payload: JsonValue = parse_json(mine.content)
            mine_items: JsonValue | None = mine_payload.get("items")
            assert mine.status_code == 200
            assert mine_items is not None and len(mine_items.items) == 1

            detail: HttpxResponse = await client.get(
                f"/api/issues/{target_issue_id}", headers=headers
            )
            detail_payload: JsonValue = parse_json(detail.content)
            exposure: JsonValue | None = detail_payload.get("exposure")
            owner: JsonValue | None = detail_payload.get("owner")
            assert detail.status_code == 200
            assert exposure is not None and exposure.kind is JsonKind.OBJECT
            assert owner is not None and owner.get("present") is not None
            present: JsonValue | None = owner.get("present")
            assert present is not None and present.as_bool() is True
            member_ref: JsonValue | None = owner.get("member_ref")
            assert member_ref is not None and member_ref.as_str() == "member:api-owner"
            assert b"email:" + b"a" * 64 not in detail.content
            assert b"a" * 64 not in detail.content
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_issue_v11_endpoints_fail_closed_until_dependencies_are_wired() -> None:
    """Return a static internal error when optional lifecycle services are absent."""
    harness: IssueHarness = await make_issue_harness()
    try:
        async with harness.db.sessions.begin() as session:
            token: CreatedToken = await TokenService(harness.audit, harness.clock).create(
                session,
                "v11-issues-unwired",
                frozenset((ApiScope.READ, ApiScope.WRITE)),
                None,
            )
        ctx: ApiContext = ApiContext(harness.db.sessions, harness.clock, harness.audit)
        app: FastAPI = FastAPI()
        install_error_handlers(app)
        app.include_router(
            build_issues_router(IssuesRouterDeps(ctx, harness.service, RecordingIndexer([], [])))
        )
        app.include_router(build_exceptions_router(ExceptionsRouterDeps(ctx)))
        app.include_router(build_policies_router(PoliciesRouterDeps(ctx)))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers: Headers = Headers({"Authorization": f"Bearer {token.token}"})
            no_member: HttpxResponse = await client.get("/api/issues?assignee=me", headers=headers)
            response: HttpxResponse = await client.post(
                "/api/issues/missing/remediation",
                headers=headers,
                json={"action": "remove"},
            )
        assert no_member.status_code == 403
        assert response.status_code == 503
        assert b'"code":"internal_error"' in response.content
        assert b"missing" not in response.content
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers: Headers = Headers({"Authorization": f"Bearer {token.token}"})
            exceptions_response: HttpxResponse = await client.get(
                "/api/exceptions", headers=headers
            )
            policies_response: HttpxResponse = await client.get("/api/policies", headers=headers)
        assert exceptions_response.status_code == 503
        assert b'"code":"internal_error"' in exceptions_response.content
        assert policies_response.status_code == 503
        assert b'"code":"internal_error"' in policies_response.content
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_issue_exception_policy_and_lifecycle_routes() -> None:
    """Exercise exception decisions, reasoned resolution, remediation, and policies."""
    harness: IssueHarness = await make_issue_harness()
    indexer: RecordingIndexer = RecordingIndexer([], [])
    owners: DirectoryOwnerResolver = DirectoryOwnerResolver()
    resolver: RecipientResolver = harness.service.resolver
    queue: SqlTaskQueue = SqlTaskQueue(harness.clock)
    decider: PlacementPolicyDecider = PlacementPolicyDecider(harness.fingerprinter, False)
    redactor: Redactor = Redactor.default()
    exception_service: ExceptionService = ExceptionService(
        harness.audit, harness.clock, redactor, owners, resolver, decider
    )
    remediation: IssueRemediationService = IssueRemediationService(
        harness.audit, harness.clock, redactor, queue, resolver, owners, decider, exception_service
    )
    policies: PolicyStore = PolicyStore(
        redactor, RuleDetector(), harness.audit, queue, resolver, harness.clock
    )
    try:
        async with harness.db.sessions.begin() as session:
            await harness.service.record_scan(
                session,
                resource_scan(
                    harness,
                    (masked_finding(harness, RuleClass.TOKEN, "remediate"),),
                    "scan-remediate",
                    resource_locator="resource:remediate",
                ),
            )
            await harness.service.record_scan(
                session,
                resource_scan(
                    harness,
                    (masked_finding(harness, RuleClass.EMAIL, "exception"),),
                    "scan-exception",
                    resource_locator="resource:exception",
                ),
            )
            await harness.service.record_scan(
                session,
                resource_scan(
                    harness,
                    (masked_finding(harness, RuleClass.PHONE, "resolve"),),
                    "scan-resolve",
                    resource_locator="resource:resolve",
                ),
            )
            session.add(
                MemberRow(
                    id="member-lifecycle",
                    handle="lifecycle",
                    display_name="Lifecycle Member",
                    email=None,
                    slack_user_id=None,
                    active=True,
                    created_at=harness.clock.now(),
                    updated_at=harness.clock.now(),
                    role="admin",
                )
            )
        async with harness.db.sessions.begin() as session:
            rows: list[IssueRow] = list((await session.scalars(select(IssueRow))).all())
            remediation_issue: IssueRow = next(
                row for row in rows if row.rule_class == RuleClass.TOKEN.value
            )
            remediation_issue.resource_ref_json = "{}"
            remediation_issue.collector_config_id = "collector-1"
            remediation_issue.assignee_member_id = "member-lifecycle"
            exception_issue: IssueRow = next(
                row for row in rows if row.rule_class == RuleClass.EMAIL.value
            )
            resolve_issue: IssueRow = next(
                row for row in rows if row.rule_class == RuleClass.PHONE.value
            )
            member_token: CreatedToken = await TokenService(harness.audit, harness.clock).create(
                session,
                "v11-lifecycle-member",
                frozenset((ApiScope.READ, ApiScope.WRITE)),
                "member-lifecycle",
            )
            admin_token: CreatedToken = await TokenService(harness.audit, harness.clock).create(
                session,
                "v11-lifecycle-admin",
                frozenset((ApiScope.READ, ApiScope.WRITE, ApiScope.ADMIN)),
                "member-lifecycle",
            )
            remediation_id: str = remediation_issue.id
            exception_id: str = exception_issue.id
            resolve_id: str = resolve_issue.id
        ctx: ApiContext = ApiContext(harness.db.sessions, harness.clock, harness.audit)
        app: FastAPI = FastAPI()
        install_error_handlers(app)
        app.include_router(
            build_issues_router(
                IssuesRouterDeps(
                    ctx,
                    harness.service,
                    indexer,
                    remediation,
                    exception_service,
                    owners,
                    resolver,
                )
            )
        )
        app.include_router(
            build_exceptions_router(ExceptionsRouterDeps(ctx, exception_service, indexer, owners))
        )
        app.include_router(build_policies_router(PoliciesRouterDeps(ctx, policies)))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            member_headers: Headers = Headers({"Authorization": f"Bearer {member_token.token}"})
            admin_headers: Headers = Headers({"Authorization": f"Bearer {admin_token.token}"})
            denied: HttpxResponse = await client.post(
                f"/api/issues/{resolve_id}/resolve",
                headers=member_headers,
                json={"reason": "manual_unverified", "note": "reviewed"},
            )
            assert denied.status_code == 403
            resolved: HttpxResponse = await client.post(
                f"/api/issues/{resolve_id}/resolve",
                headers=admin_headers,
                json={"reason": "manual_unverified", "note": "reviewed"},
            )
            assert resolved.status_code == 200
            assert b'"resolution":"manual_unverified"' in resolved.content
            patch_resolved: HttpxResponse = await client.patch(
                f"/api/issues/{resolve_id}",
                headers=member_headers,
                json={"status": "ignored"},
            )
            assert patch_resolved.status_code == 422
            assert b"invalid_transition" in patch_resolved.content
            remediation_resolved: HttpxResponse = await client.post(
                f"/api/issues/{resolve_id}/remediation",
                headers=member_headers,
                json={"action": "remove"},
            )
            assert remediation_resolved.status_code == 422
            assert b"invalid_transition" in remediation_resolved.content

            released: HttpxResponse = await client.post(
                f"/api/issues/{remediation_id}/release", headers=member_headers
            )
            assert released.status_code == 200
            async with harness.db.sessions() as session:
                released_issue: IssueRow | None = await session.get(IssueRow, remediation_id)
            assert released_issue is not None
            assert released_issue.assignee_member_id is None

            remediation_response: HttpxResponse = await client.post(
                f"/api/issues/{remediation_id}/remediation",
                headers=member_headers,
                json={"action": "remove", "include_linked": False},
            )
            assert remediation_response.status_code == 200
            assert b'"rescans_enqueued":1' in remediation_response.content
            assert remediation_id in indexer.issue_ids
            async with harness.db.sessions() as session:
                remediated: IssueRow | None = await session.get(IssueRow, remediation_id)
            assert remediated is not None
            assert remediated.status == "pending_verification"

            requested: HttpxResponse = await client.post(
                f"/api/issues/{exception_id}/exceptions",
                headers=member_headers,
                json={"reason": "Temporary business need", "duration": "30d"},
            )
            assert requested.status_code == 201
            exception_payload: JsonValue = parse_json(requested.content)
            exception_value: JsonValue | None = exception_payload.get("id")
            assert exception_value is not None and exception_value.as_str() is not None
            exception_row_id: str = exception_value.as_str() or ""
            exception_detail: HttpxResponse = await client.get(
                f"/api/issues/{exception_id}", headers=member_headers
            )
            exception_detail_payload: JsonValue = parse_json(exception_detail.content)
            active_exception: JsonValue | None = exception_detail_payload.get("active_exception")
            assert exception_detail.status_code == 200
            assert active_exception is not None
            active_status: JsonValue | None = active_exception.get("status")
            assert active_status is not None and active_status.as_str() == "requested"
            approved: HttpxResponse = await client.post(
                f"/api/exceptions/{exception_row_id}/approve",
                headers=admin_headers,
                json={},
            )
            assert approved.status_code == 200
            listing: HttpxResponse = await client.get(
                "/api/exceptions?limit=10", headers=member_headers
            )
            assert listing.status_code == 200
            assert b'"status":"active"' in listing.content
            async with harness.db.sessions() as session:
                excepted: IssueRow | None = await session.get(IssueRow, exception_id)
            assert excepted is not None and excepted.status == "ignored"

            source: str = '{"name":"api-policy","effect":"violate"}'
            created_policy: HttpxResponse = await client.post(
                "/api/policies",
                headers=admin_headers,
                json={"source_format": SourceFormat.JSON.value, "source_text": source},
            )
            assert created_policy.status_code == 201
            assert b'"version":1' in created_policy.content
            activated: HttpxResponse = await client.post(
                "/api/policies/api-policy/activate",
                headers=admin_headers,
                json={"version": 1},
            )
            assert activated.status_code == 200
            invalid_policy: HttpxResponse = await client.post(
                "/api/policies/validate",
                headers=member_headers,
                json={
                    "source_format": "json",
                    "source_text": '{"name":"Bad_Name","effect":"unsupported"}',
                },
            )
            invalid_payload: JsonValue = parse_json(invalid_policy.content)
            invalid_errors: JsonValue | None = invalid_payload.get("errors")
            assert invalid_policy.status_code == 200
            assert invalid_errors is not None and len(invalid_errors.items) > 0
            assert invalid_errors.items[0].get("path") is not None
            assert invalid_errors.items[0].get("code") is not None
            forbidden_create: HttpxResponse = await client.post(
                "/api/policies",
                headers=member_headers,
                json={"source_format": "json", "source_text": source},
            )
            assert forbidden_create.status_code == 403
            secret: str = github_token().value
            leaked_source: str = (
                '{"name":"blocked-policy","effect":"violate","rationale":"' + secret + '"}'
            )
            rejected: HttpxResponse = await client.post(
                "/api/policies",
                headers=admin_headers,
                json={"source_format": "json", "source_text": leaked_source},
            )
            assert rejected.status_code == 422
            assert b"raw_value_detected" in rejected.content
            assert_no_leaks(rejected.text, "policy validation response")
            assert secret not in rejected.text
            assert b"blocked-policy" not in rejected.content
    finally:
        await harness.close()
