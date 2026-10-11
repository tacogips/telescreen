"""Template versions: create, activate, resolve, catalog lookup and rejection audit."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from telescreen.core.clock import FakeClock
from telescreen.core.errors import TelescreenError
from telescreen.core.json import dump_json
from telescreen.core.vocabulary import ErrorCode, NotificationEvent
from telescreen.db.models.notifications import NotificationTemplateRow
from telescreen.notifications.templates.model import (
    ResolvedTemplate,
    TemplateIssue,
    TemplateIssueCode,
    TemplateTexts,
    TemplateValidationError,
)
from telescreen.notifications.templates.resolver import (
    BuiltinTemplateResolver,
    DbTemplateResolver,
)
from telescreen.notifications.templates.service import (
    TemplateDetail,
    TemplateService,
    TemplateSummary,
    TemplateVersionView,
)
from telescreen.ports.templates import TemplateInfo
from tests.notifications.v12.fakes import RecordingAudit, make_templates
from tests.support.db import SqliteHarness, make_sqlite_harness
from tests.support.samples import assert_no_leaks, slack_token

DIGEST: NotificationEvent = NotificationEvent.OWNER_DIGEST
NOTIFY: NotificationEvent = NotificationEvent.WORKFLOW_NOTIFY


def clock() -> FakeClock:
    """Return a fixed test clock."""
    return FakeClock(datetime(2026, 10, 11, tzinfo=UTC))


async def test_versions_are_inactive_until_activated_and_resolver_follows() -> None:
    """Create v1 and v2 inactive, activate v2, and resolve v2."""
    harness: SqliteHarness = await make_sqlite_harness()
    audit: RecordingAudit = RecordingAudit()
    service: TemplateService = make_templates(audit, clock())
    resolver: DbTemplateResolver = DbTemplateResolver()
    async with harness.sessions.begin() as session:
        first: tuple[str, int] = await service.create_version(
            session,
            "team.alert",
            NOTIFY,
            TemplateTexts("v1 {{ issue.short_id }}", "b1"),
            "member:a",
        )
        second: tuple[str, int] = await service.create_version(
            session,
            "team.alert",
            NOTIFY,
            TemplateTexts("v2 {{ issue.short_id }}", "b2"),
            "member:a",
        )
        assert (first, second) == (("team.alert", 1), ("team.alert", 2))
        inactive: ResolvedTemplate = await resolver.resolve(session, "team.alert", NOTIFY)
        assert inactive.fallback and inactive.name == "system.workflow_notify"
        assert await service.lookup(session, "team.alert") == TemplateInfo(
            "team.alert", NOTIFY, None
        )
        await service.activate(session, "team.alert", 2, "member:a")
    async with harness.sessions() as session:
        resolved: ResolvedTemplate = await resolver.resolve(session, "team.alert", NOTIFY)
        info: TemplateInfo | None = await service.lookup(session, "team.alert")
        detail: TemplateDetail = await service.get(session, "team.alert")
        view: TemplateVersionView = await service.version_text(session, "team.alert", 1)
    assert (resolved.name, resolved.version, resolved.fallback) == ("team.alert", 2, False)
    assert resolved.texts.subject == "v2 {{ issue.short_id }}"
    assert info == TemplateInfo("team.alert", NOTIFY, 2)
    assert detail.active_version == 2 and not detail.builtin
    assert [(item.version, item.active) for item in detail.versions] == [(1, False), (2, True)]
    assert "issue.short_id" in detail.variables
    assert view.texts.body == "b1" and not view.active
    assert [entry.action for entry in audit.entries] == [
        "template.created",
        "template.created",
        "template.activated",
    ]
    assert dump_json(audit.entries[-1].details) == (
        '{"event_kind":"workflow_notify","name":"team.alert","version":2}'
    )
    await harness.close()


async def test_builtin_override_and_restore_with_version_zero() -> None:
    """A DB version overrides system.owner_digest; activating 0 restores the built-in."""
    harness: SqliteHarness = await make_sqlite_harness()
    service: TemplateService = make_templates(RecordingAudit(), clock())
    resolver: DbTemplateResolver = DbTemplateResolver()
    async with harness.sessions.begin() as session:
        await service.create_version(
            session,
            "system.owner_digest",
            DIGEST,
            TemplateTexts("Digest", "{{ digest.summary }}"),
            "system:test",
        )
        await service.activate(session, "system.owner_digest", 1, "system:test")
        overridden: ResolvedTemplate = await resolver.resolve(
            session, "system.owner_digest", DIGEST
        )
        await service.activate(session, "system.owner_digest", 0, "system:test")
        restored: ResolvedTemplate = await resolver.resolve(session, "system.owner_digest", DIGEST)
        rows: list[NotificationTemplateRow] = list(
            (await session.scalars(select(NotificationTemplateRow))).all()
        )
        info: TemplateInfo | None = await service.lookup(session, "system.owner_digest")
    assert (overridden.version, overridden.texts.subject) == (1, "Digest")
    assert (restored.version, restored.texts.subject) == (0, "Telescreen owner digest")
    assert not restored.fallback
    assert [row.active for row in rows] == [False]
    assert info == TemplateInfo("system.owner_digest", DIGEST, 0)
    await harness.close()


async def test_activate_unknown_version_or_name_is_not_found() -> None:
    """Unknown versions and unknown names raise not_found."""
    harness: SqliteHarness = await make_sqlite_harness()
    service: TemplateService = make_templates(RecordingAudit(), clock())
    async with harness.sessions.begin() as session:
        await service.create_version(session, "team.alert", NOTIFY, TemplateTexts("s", "b"), "m")
        for name, version in (("team.alert", 5), ("team.alert", 0), ("missing", 1)):
            with pytest.raises(TelescreenError) as raised:
                await service.activate(session, name, version, "member:a")
            assert raised.value.code is ErrorCode.NOT_FOUND
        with pytest.raises(TelescreenError) as missing:
            await service.get(session, "missing")
        assert missing.value.code is ErrorCode.NOT_FOUND
        assert await service.lookup(session, "missing") is None
    await harness.close()


async def test_rejected_version_writes_no_row_and_audits_field_names_only() -> None:
    """A sample secret in slack_body is rejected with a field-only audit record."""
    harness: SqliteHarness = await make_sqlite_harness()
    audit: RecordingAudit = RecordingAudit()
    service: TemplateService = make_templates(audit, clock())
    raw: str = slack_token().value
    async with harness.sessions.begin() as session:
        with pytest.raises(TemplateValidationError) as raised:
            await service.create_version(
                session,
                "team.alert",
                NOTIFY,
                TemplateTexts("ok", "ok", slack_body=f"token {raw}"),
                "member:a",
            )
        rows: list[NotificationTemplateRow] = list(
            (await session.scalars(select(NotificationTemplateRow))).all()
        )
    assert raised.value.issues == (
        TemplateIssue("slack_body", TemplateIssueCode.RAW_VALUE_DETECTED),
    )
    assert rows == []
    assert [entry.action for entry in audit.entries] == ["template.rejected"]
    details: str = dump_json(audit.entries[0].details)
    assert details == '{"codes":["raw_value_detected"],"fields":["slack_body"]}'
    assert_no_leaks(details + repr(audit.entries) + str(raised.value), "template rejection")
    await harness.close()


async def test_rejections_for_reserved_name_and_event_mismatch() -> None:
    """system.custom is reserved; a new version must keep the name's event kind."""
    harness: SqliteHarness = await make_sqlite_harness()
    audit: RecordingAudit = RecordingAudit()
    service: TemplateService = make_templates(audit, clock())
    async with harness.sessions.begin() as session:
        with pytest.raises(TemplateValidationError) as reserved:
            await service.create_version(
                session, "system.custom", NOTIFY, TemplateTexts("s", "b"), "m"
            )
        await service.create_version(session, "team.alert", NOTIFY, TemplateTexts("s", "b"), "m")
        with pytest.raises(TemplateValidationError) as mismatch:
            await service.create_version(
                session, "team.alert", DIGEST, TemplateTexts("s", "b"), "m"
            )
        with pytest.raises(TemplateValidationError) as builtin_mismatch:
            await service.create_version(
                session, "system.owner_digest", NOTIFY, TemplateTexts("s", "b"), "m"
            )
    assert reserved.value.issues == (TemplateIssue("name", TemplateIssueCode.RESERVED_NAME),)
    assert mismatch.value.issues == (
        TemplateIssue("event_kind", TemplateIssueCode.TEMPLATE_EVENT_MISMATCH),
    )
    assert builtin_mismatch.value.issues == mismatch.value.issues
    assert audit.entries[0].target_id == "invalid"
    await harness.close()


async def test_list_merges_builtins_with_stored_names() -> None:
    """The catalog lists every built-in plus stored custom names."""
    harness: SqliteHarness = await make_sqlite_harness()
    service: TemplateService = make_templates(RecordingAudit(), clock())
    async with harness.sessions.begin() as session:
        await service.create_version(
            session,
            "team.alert",
            NOTIFY,
            TemplateTexts("s", "b", description="Team alert"),
            "member:a",
        )
        summaries: tuple[TemplateSummary, ...] = await service.list_templates(session)
    assert len(summaries) == len(NotificationEvent) + 1
    digest: TemplateSummary = next(item for item in summaries if item.name == "system.owner_digest")
    assert digest.builtin and digest.active_version == 0
    custom: TemplateSummary = next(item for item in summaries if item.name == "team.alert")
    assert (custom.builtin, custom.active_version, custom.latest_version) == (False, None, 1)
    assert custom.description == "Team alert" and custom.updated_at is not None
    await harness.close()


async def test_builtin_resolver_falls_back_for_mismatched_names() -> None:
    """Unknown or mismatched names fall back to the event built-in."""
    harness: SqliteHarness = await make_sqlite_harness()
    resolver: BuiltinTemplateResolver = BuiltinTemplateResolver()
    async with harness.sessions() as session:
        exact: ResolvedTemplate = await resolver.resolve(session, "system.owner_digest", DIGEST)
        other: ResolvedTemplate = await resolver.resolve(session, "system.owner_digest", NOTIFY)
        custom: ResolvedTemplate = await resolver.resolve(session, "team.alert", NOTIFY)
    assert (exact.name, exact.fallback) == ("system.owner_digest", False)
    assert (other.name, other.fallback) == ("system.workflow_notify", True)
    assert (custom.name, custom.fallback) == ("system.workflow_notify", True)
    await harness.close()
