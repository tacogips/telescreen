"""Template validation, rendering rules, synthetic contexts and preview."""

from dataclasses import fields
from datetime import UTC, datetime

import pytest

from telescreen.core.clock import FakeClock
from telescreen.core.vocabulary import DlpVerdict, NotificationChannel, NotificationEvent
from telescreen.masking.redact import Redactor
from telescreen.notifications.templates.builtins import builtin_for
from telescreen.notifications.templates.model import (
    PreviewResult,
    TemplateIssue,
    TemplateIssueCode,
    TemplateTexts,
    TemplateValidationError,
)
from telescreen.notifications.templates.render import render_for_channel
from telescreen.notifications.templates.service import TemplateService
from telescreen.notifications.templates.synthetic import synthetic_context
from telescreen.notifications.templates.validate import validate_template
from telescreen.notifications.templates.variables import VARIABLE_FIELDS, allowed_paths, lookup
from telescreen.ports.notifications import NotificationContext
from tests.notifications.v12.fakes import RecordingAudit, make_templates
from tests.support.samples import Sample, all_samples, assert_no_leaks, github_token

REDACTOR: Redactor = Redactor.default()


def issues_of(
    texts: TemplateTexts,
    event: NotificationEvent = NotificationEvent.WORKFLOW_NOTIFY,
    name: str | None = "custom.alert",
    existing: NotificationEvent | None = None,
) -> tuple[TemplateIssue, ...]:
    """Validate a candidate template."""
    return validate_template(name, event, texts, existing, REDACTOR)


def test_variable_table_matches_context_fields() -> None:
    """All 21 variables map to distinct NotificationContext fields."""
    context_fields: set[str] = {item.name for item in fields(NotificationContext)}
    assert len(VARIABLE_FIELDS) == 21
    assert {pair[1] for pair in VARIABLE_FIELDS} == context_fields
    assert lookup(NotificationContext(issue_risk_score=7), "issue.risk_score") == "7"
    assert lookup(NotificationContext(), "issue.title") == ""


def test_valid_template_has_no_issues() -> None:
    """A template using only allowed variables passes."""
    assert issues_of(TemplateTexts("{{ issue.short_id }}", "{{ step.name }} {{ task.link }}")) == ()


def test_unknown_variable_is_reported_on_its_field() -> None:
    """A path outside the allow-list is unknown_variable on that field."""
    assert issues_of(TemplateTexts("ok", "{{ issue.secret }}")) == (
        TemplateIssue("body", TemplateIssueCode.UNKNOWN_VARIABLE),
    )


def test_variable_allowed_for_other_event_is_unknown() -> None:
    """digest.summary is not allowed for exception_expired."""
    assert issues_of(
        TemplateTexts("ok", "{{ digest.summary }}"), NotificationEvent.EXCEPTION_EXPIRED
    ) == (TemplateIssue("body", TemplateIssueCode.UNKNOWN_VARIABLE),)
    assert "digest.summary" not in allowed_paths(NotificationEvent.EXCEPTION_EXPIRED)
    assert allowed_paths(NotificationEvent.OWNER_DIGEST) == ("digest.summary",)


@pytest.mark.parametrize(
    "field_name", ("subject", "body", "email_subject", "email_body", "slack_body", "description")
)
def test_raw_value_in_any_text_field_is_detected(field_name: str) -> None:
    """A sample secret in any text field is raw_value_detected and never echoed."""
    raw: str = github_token().value
    values: list[str | None] = ["ok", "ok", None, None, None, None]
    names: tuple[str, ...] = (
        "subject",
        "body",
        "email_subject",
        "email_body",
        "slack_body",
        "description",
    )
    values[names.index(field_name)] = f"value {raw}"
    texts: TemplateTexts = TemplateTexts(
        values[0] or "", values[1] or "", values[2], values[3], values[4], values[5]
    )
    found: tuple[TemplateIssue, ...] = issues_of(texts)
    assert TemplateIssue(field_name, TemplateIssueCode.RAW_VALUE_DETECTED) in found
    assert_no_leaks(repr(found) + repr(texts), "template issues")


def test_name_rules() -> None:
    """Names follow the slug pattern; new system.* names are reserved."""
    texts: TemplateTexts = TemplateTexts("ok", "ok")
    assert issues_of(texts, name="system.custom") == (
        TemplateIssue("name", TemplateIssueCode.RESERVED_NAME),
    )
    assert issues_of(texts, name="Bad Name") == (
        TemplateIssue("name", TemplateIssueCode.INVALID_NAME),
    )
    assert issues_of(texts, NotificationEvent.WORKFLOW_NOTIFY, "system.workflow_notify") == ()


def test_event_mismatch_length_syntax_and_variants() -> None:
    """Event mismatch, too long, invalid syntax and empty variants are reported."""
    assert issues_of(TemplateTexts("ok", "ok"), existing=NotificationEvent.OWNER_DIGEST) == (
        TemplateIssue("event_kind", TemplateIssueCode.TEMPLATE_EVENT_MISMATCH),
    )
    assert issues_of(TemplateTexts("x" * 201, "ok")) == (
        TemplateIssue("subject", TemplateIssueCode.TOO_LONG),
    )
    assert issues_of(TemplateTexts("ok", "x" * 4001)) == (
        TemplateIssue("body", TemplateIssueCode.TOO_LONG),
    )
    assert issues_of(TemplateTexts("ok", "{{ issue.title")) == (
        TemplateIssue("body", TemplateIssueCode.INVALID_TEMPLATE),
    )
    assert issues_of(TemplateTexts("ok", "{{ issue..title }}")) == (
        TemplateIssue("body", TemplateIssueCode.INVALID_TEMPLATE),
    )
    assert issues_of(TemplateTexts("ok", "ok", email_subject="")) == (
        TemplateIssue("email_subject", TemplateIssueCode.INCOMPLETE_VARIANT),
    )
    assert issues_of(TemplateTexts("ok", "ok", email_body="body only")) == ()


def test_slack_escapes_values_but_not_literal_text() -> None:
    """Slack escapes &, < and > in substituted values only."""
    texts: TemplateTexts = TemplateTexts("A & B {{ issue.short_id }}", "<{{ issue.title }}>")
    context: NotificationContext = NotificationContext(
        issue_short_id="TELE-1", issue_title="<!channel> & <@U1>"
    )
    slack: tuple[str, str] = render_for_channel(
        texts, NotificationChannel.SLACK, context, NotificationEvent.WORKFLOW_NOTIFY
    )
    assert slack == ("A & B TELE-1", "<&lt;!channel&gt; &amp; &lt;@U1&gt;>")
    email: tuple[str, str] = render_for_channel(
        texts, NotificationChannel.EMAIL, context, NotificationEvent.WORKFLOW_NOTIFY
    )
    assert email == ("A & B TELE-1", "<<!channel> & <@U1>>")


def test_subject_is_single_line_body_is_rstripped_and_missing_is_empty() -> None:
    """Subject newlines become spaces; the body loses trailing whitespace."""
    texts: TemplateTexts = TemplateTexts(
        "{{ issue.title }}", "{{ issue.short_id }}\n{{ issue.link }}\n"
    )
    context: NotificationContext = NotificationContext(
        issue_short_id="TELE-9", issue_title="line one\r\nline two"
    )
    assert render_for_channel(
        texts, NotificationChannel.EMAIL, context, NotificationEvent.WORKFLOW_NOTIFY
    ) == ("line one  line two", "TELE-9")


def test_channel_variants_are_selected() -> None:
    """Email uses its variants; Slack uses the shared subject and its body."""
    texts: TemplateTexts = TemplateTexts("S", "B", "ES", "EB", "SB")
    event: NotificationEvent = NotificationEvent.OWNER_DIGEST
    context: NotificationContext = NotificationContext()
    assert render_for_channel(texts, NotificationChannel.EMAIL, context, event) == ("ES", "EB")
    assert render_for_channel(texts, NotificationChannel.SLACK, context, event) == ("S", "SB")


def test_render_ignores_variables_outside_event_allow_list() -> None:
    """A stored text never renders a value outside its event's allow-list."""
    texts: TemplateTexts = TemplateTexts("{{ issue.title }}", "{{ digest.summary }}")
    context: NotificationContext = NotificationContext(
        issue_title="title", digest_summary="summary"
    )
    assert render_for_channel(
        texts, NotificationChannel.EMAIL, context, NotificationEvent.OWNER_DIGEST
    ) == ("", "summary")


@pytest.mark.parametrize("event", tuple(NotificationEvent))
def test_synthetic_contexts_contain_no_detected_values(event: NotificationEvent) -> None:
    """Every synthetic field passes the rule detector and no sample appears."""
    context: NotificationContext = synthetic_context(event)
    for item in fields(NotificationContext):
        value: str | int | None = getattr(context, item.name)
        if isinstance(value, str):
            assert not REDACTOR.has_hits(value), item.name
    assert_no_leaks(repr(context), "synthetic context")


@pytest.mark.parametrize("event", tuple(NotificationEvent))
async def test_preview_renders_both_channels_with_pass_verdict(event: NotificationEvent) -> None:
    """The built-in of every event previews cleanly on email and Slack."""
    service: TemplateService = make_templates(
        RecordingAudit(), FakeClock(datetime(2026, 10, 11, tzinfo=UTC))
    )
    result: PreviewResult = await service.preview(event, builtin_for(event).texts)
    assert result.email.dlp_verdict is DlpVerdict.PASS
    assert result.slack.dlp_verdict is DlpVerdict.PASS
    assert result.email.subject
    assert result.slack.body


async def test_preview_rejects_samples_without_echo() -> None:
    """Preview refuses texts containing any rule-detectable sample value."""
    service: TemplateService = make_templates(
        RecordingAudit(), FakeClock(datetime(2026, 10, 11, tzinfo=UTC))
    )
    detectable: tuple[Sample, ...] = tuple(
        sample for sample in all_samples() if REDACTOR.has_hits(sample.value)
    )
    assert len(detectable) >= 5
    for sample in detectable:
        with pytest.raises(TemplateValidationError) as raised:
            await service.preview(
                NotificationEvent.WORKFLOW_NOTIFY, TemplateTexts("ok", f"x {sample.value} y")
            )
        assert TemplateIssue("body", TemplateIssueCode.RAW_VALUE_DETECTED) in (
            raised.value.issues
        ), sample.name
        assert_no_leaks(str(raised.value) + repr(raised.value.issues), "preview error")
