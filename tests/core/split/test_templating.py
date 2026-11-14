"""Tests for shared pure template contracts."""

from telescreen.core.vocabulary import NotificationChannel, NotificationEvent
from telescreen.notifications.templates.model import TemplateTexts
from telescreen.notifications.templates.render import (
    channel_texts,
    field_occurrences,
    render_for_channel,
)
from telescreen.ports.notifications import NotificationContext


def test_channel_texts_and_field_occurrences_use_selected_variants() -> None:
    """Template counts follow the email override and rendering selection."""
    texts: TemplateTexts = TemplateTexts(
        "Subject",
        "Body",
        email_subject="Email",
        email_body="{{ issue.short_id }} {{ issue.short_id }}",
    )
    assert channel_texts(texts, NotificationChannel.EMAIL) == (
        "Email",
        "{{ issue.short_id }} {{ issue.short_id }}",
    )
    assert field_occurrences(texts, NotificationChannel.EMAIL) == (("issue_short_id", 2),)
    rendered: tuple[str, str] = render_for_channel(
        texts,
        NotificationChannel.EMAIL,
        NotificationContext(issue_short_id="ISSUE-1"),
        NotificationEvent.WORKFLOW_NOTIFY,
    )
    assert rendered == ("Email", "ISSUE-1 ISSUE-1")


def test_slack_channel_texts_match_rendered_variant() -> None:
    """Slack text selection retains the existing optional body behavior."""
    texts: TemplateTexts = TemplateTexts("Subject", "Body", slack_body="Slack body")
    assert channel_texts(texts, NotificationChannel.SLACK) == ("Subject", "Slack body")
    assert render_for_channel(
        texts, NotificationChannel.SLACK, NotificationContext(), NotificationEvent.WORKFLOW_NOTIFY
    ) == ("Subject", "Slack body")
