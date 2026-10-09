"""Audit chain, redaction and incremental search tests."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Headers, Response
from sqlalchemy import select, text

from telescreen.api.common import ApiContext, install_error_handlers
from telescreen.api.routers.audit import AuditRouterDeps, build_audit_router
from telescreen.audit.service import AuditLogService
from telescreen.audit.verify import ChainVerification, verify_chain
from telescreen.core.clock import FakeClock, ensure_utc
from telescreen.core.json import JsonValue, json_array, json_object, json_str, parse_json
from telescreen.core.vocabulary import ActorKind, ApiScope
from telescreen.db.models import AuditRecordRow
from telescreen.detection import RuleDetector
from telescreen.directory import CreatedToken, TokenService
from telescreen.masking.index_guard import IndexGuard
from telescreen.masking.redact import Redactor
from telescreen.ports.audit import AuditEntry
from telescreen.search.audit_tick import AuditIndexSchedule
from telescreen.search.backend import IndexName, SearchPage, SearchQuery
from telescreen.search.memory import InMemorySearchBackend
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import Sample, assert_no_leaks, github_token


@pytest.mark.asyncio
async def test_append_redacts_nested_details_and_chain_verifies() -> None:
    """Audit append masks every string leaf and creates a valid sequence chain."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))
    audit: AuditLogService = AuditLogService(clock, Redactor(RuleDetector()))
    sample: Sample = github_token()
    async with harness.sessions.begin() as session:
        first: int = await audit.append(
            session,
            AuditEntry(
                ActorKind.SYSTEM,
                "system:test",
                "test.append",
                "issue",
                "issue-1",
                f"checked {sample.value}",
                json_object((("nested", json_array((json_str("safe"), json_str(sample.value)))),)),
            ),
        )
        second: int = await audit.append(
            session,
            AuditEntry(
                ActorKind.SYSTEM,
                "system:test",
                "test.append",
                "issue",
                "issue-2",
                "clean summary",
                json_object(()),
            ),
        )
    assert (first, second) == (1, 2)
    async with harness.sessions() as session:
        result: ChainVerification = await verify_chain(session)
        rows: list[AuditRecordRow] = list(
            (await session.scalars(select(AuditRecordRow).order_by(AuditRecordRow.seq))).all()
        )
    assert result == ChainVerification(True, None, 2)
    async with harness.sessions() as session:
        stored: AuditRecordRow | None = await session.get(AuditRecordRow, 1)
        assert stored is not None
        assert_no_leaks(stored.summary + stored.details_json, "audit record")
    assert len(rows) == 2
    await harness.close()


@pytest.mark.asyncio
async def test_tamper_detection_reports_first_broken_sequence() -> None:
    """Stored changes are detected at the first changed sequence number."""
    harness: SqliteHarness = await make_sqlite_harness()
    audit: AuditLogService = AuditLogService(
        FakeClock(datetime(2026, 10, 9, tzinfo=UTC)), Redactor(RuleDetector())
    )
    async with harness.sessions.begin() as session:
        for number in range(1, 4):
            await audit.append(
                session,
                AuditEntry(
                    ActorKind.SYSTEM,
                    "system:test",
                    "test.append",
                    "issue",
                    f"issue-{number}",
                    f"entry {number}",
                    json_object(()),
                ),
            )
    async with harness.sessions.begin() as session:
        await session.execute(text("UPDATE audit_records SET summary = 'tampered' WHERE seq = 2"))
    async with harness.sessions() as session:
        result: ChainVerification = await verify_chain(session)
    assert result == ChainVerification(False, 2, 2)
    await harness.close()


@pytest.mark.asyncio
async def test_audit_schedule_indexes_new_records() -> None:
    """Schedule tick indexes later audit sequences and preserves searchable fields."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, tzinfo=UTC))
    audit: AuditLogService = AuditLogService(clock, Redactor(RuleDetector()))
    async with harness.sessions.begin() as session:
        await audit.append(
            session,
            AuditEntry(
                ActorKind.SYSTEM,
                "system:test",
                "test.action",
                "issue",
                "issue-1",
                "safe summary",
                json_object(()),
            ),
        )
    backend: InMemorySearchBackend = InMemorySearchBackend()
    schedule: AuditIndexSchedule = AuditIndexSchedule(
        harness.sessions, backend, IndexGuard(RuleDetector()), audit
    )
    async with harness.sessions() as session:
        indexed: int = await schedule.tick(session, clock.now())
    page: SearchPage = await backend.search(IndexName.AUDIT, SearchQuery("safe summary"))
    assert indexed == 1
    assert page.total == 1
    assert page.hits[0].get("action") == "test.action"
    await harness.close()


@pytest.mark.asyncio
async def test_audit_api_search_and_verify_use_indexed_records() -> None:
    """Exercise the read-scoped audit endpoints through an in-process ASGI app."""
    harness: SqliteHarness = await make_sqlite_harness()
    clock: FakeClock = FakeClock(datetime(2026, 10, 9, 12, tzinfo=UTC))
    audit: AuditLogService = AuditLogService(clock, Redactor(RuleDetector()))
    tokens: TokenService = TokenService(audit, clock)
    async with harness.sessions.begin() as session:
        read_token: CreatedToken = await tokens.create(
            session, "audit-read", frozenset((ApiScope.READ,)), None
        )
        await audit.append(
            session,
            AuditEntry(
                ActorKind.MEMBER,
                "member:alice",
                "issue.acknowledged",
                "issue",
                "issue-1",
                "Issue acknowledged by Alice",
                json_object(()),
            ),
        )
        await audit.append(
            session,
            AuditEntry(
                ActorKind.MEMBER,
                "member:bob",
                "issue.resolved",
                "issue",
                "issue-2",
                "Issue resolved by Bob",
                json_object(()),
            ),
        )

    backend: InMemorySearchBackend = InMemorySearchBackend()
    schedule: AuditIndexSchedule = AuditIndexSchedule(
        harness.sessions, backend, IndexGuard(RuleDetector()), audit
    )
    async with harness.sessions() as session:
        indexed: int = await schedule.tick(session, clock.now())
    assert indexed == 3

    ctx: ApiContext = ApiContext(harness.sessions, clock, audit)
    app: FastAPI = FastAPI()
    install_error_handlers(app)
    app.include_router(build_audit_router(AuditRouterDeps(ctx, backend)))
    transport: ASGITransport = ASGITransport(app=app, raise_app_exceptions=False)
    before: datetime = clock.now() - timedelta(minutes=1)
    after: datetime = clock.now() + timedelta(minutes=1)
    read_headers: Headers = Headers({"Authorization": f"Bearer {read_token.token}"})
    async with harness.sessions() as session:
        action_record: AuditRecordRow | None = await session.get(AuditRecordRow, 2)
    assert action_record is not None
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        action_response: Response = await client.get(
            "/api/audit/search", params={"action": "issue.acknowledged"}, headers=read_headers
        )
        assert action_response.status_code == 200
        action_body: JsonValue = parse_json(action_response.content)
        action_total: JsonValue | None = action_body.get("total")
        action_items: JsonValue | None = action_body.get("items")
        assert action_total is not None and action_total.as_int() == 1
        assert action_items is not None and action_items.as_list() is not None
        action_item: JsonValue = action_items.items[0]
        action_id: JsonValue | None = action_item.get("id")
        action_seq: JsonValue | None = action_item.get("seq")
        action_at: JsonValue | None = action_item.get("at")
        action_actor: JsonValue | None = action_item.get("actor_ref")
        action_summary: JsonValue | None = action_item.get("summary")
        assert action_id is not None and action_id.as_str() == action_record.id
        assert action_seq is not None and action_seq.as_int() == action_record.seq
        assert (
            action_at is not None and action_at.as_str() == ensure_utc(action_record.at).isoformat()
        )
        assert action_actor is not None and action_actor.as_str() == action_record.actor_ref
        assert action_summary is not None
        assert action_summary.as_str() == action_record.summary

        actor_response: Response = await client.get(
            "/api/audit/search", params={"actor": "member:bob"}, headers=read_headers
        )
        actor_body: JsonValue = parse_json(actor_response.content)
        actor_total: JsonValue | None = actor_body.get("total")
        actor_items: JsonValue | None = actor_body.get("items")
        assert actor_response.status_code == 200
        assert actor_total is not None and actor_total.as_int() == 1
        assert actor_items is not None and actor_items.as_list() is not None
        actor_item: JsonValue = actor_items.items[0]
        actor_action: JsonValue | None = actor_item.get("action")
        actor_ref: JsonValue | None = actor_item.get("actor_ref")
        assert actor_action is not None and actor_action.as_str() == "issue.resolved"
        assert actor_ref is not None and actor_ref.as_str() == "member:bob"

        from_before: Response = await client.get(
            "/api/audit/search", params={"from": before.isoformat()}, headers=read_headers
        )
        from_before_body: JsonValue = parse_json(from_before.content)
        from_before_total: JsonValue | None = from_before_body.get("total")
        assert from_before.status_code == 200
        assert from_before_total is not None and from_before_total.as_int() == 3

        from_after: Response = await client.get(
            "/api/audit/search", params={"from": after.isoformat()}, headers=read_headers
        )
        from_after_body: JsonValue = parse_json(from_after.content)
        from_after_total: JsonValue | None = from_after_body.get("total")
        assert from_after.status_code == 200
        assert from_after_total is not None and from_after_total.as_int() == 0

        to_before: Response = await client.get(
            "/api/audit/search", params={"to": before.isoformat()}, headers=read_headers
        )
        to_before_body: JsonValue = parse_json(to_before.content)
        to_before_total: JsonValue | None = to_before_body.get("total")
        assert to_before.status_code == 200
        assert to_before_total is not None and to_before_total.as_int() == 0

        to_after: Response = await client.get(
            "/api/audit/search", params={"to": after.isoformat()}, headers=read_headers
        )
        to_after_body: JsonValue = parse_json(to_after.content)
        to_after_total: JsonValue | None = to_after_body.get("total")
        assert to_after.status_code == 200
        assert to_after_total is not None and to_after_total.as_int() == 3

        verify_response: Response = await client.get("/api/audit/verify", headers=read_headers)
        verify_body: JsonValue = parse_json(verify_response.content)
        verify_ok: JsonValue | None = verify_body.get("ok")
        first_broken_seq: JsonValue | None = verify_body.get("first_broken_seq")
        checked: JsonValue | None = verify_body.get("checked")
        assert verify_response.status_code == 200
        assert verify_ok is not None and verify_ok.as_bool() is True
        assert first_broken_seq is not None and first_broken_seq.is_null()
        assert checked is not None and checked.as_int() == 3

        unauthorized: Response = await client.get("/api/audit/search")
        assert unauthorized.status_code == 401
    await harness.close()
