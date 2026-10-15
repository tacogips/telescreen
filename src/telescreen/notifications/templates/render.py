"""Channel-specific rendering of template text from an allow-listed context."""

from telescreen.core.templating import Template as Template
from telescreen.core.templating import parse_template as parse_template
from telescreen.core.vocabulary import NotificationChannel, NotificationEvent
from telescreen.notifications.templates.model import TemplateTexts
from telescreen.notifications.templates.variables import VARIABLE_FIELDS, allowed_paths, lookup
from telescreen.ports.notifications import NotificationContext


def channel_texts(texts: TemplateTexts, channel: NotificationChannel) -> tuple[str, str]:
    """Select the subject and body variant used for one delivery channel."""
    subject: str = texts.subject
    body: str = texts.body
    if channel is NotificationChannel.EMAIL:
        subject = texts.email_subject if texts.email_subject is not None else subject
        body = texts.email_body if texts.email_body is not None else body
    elif texts.slack_body is not None:
        body = texts.slack_body
    return subject, body


def field_occurrences(
    texts: TemplateTexts, channel: NotificationChannel
) -> tuple[tuple[str, int], ...]:
    """Count context fields referenced in the selected subject and body templates."""
    subject: str
    body: str
    subject, body = channel_texts(texts, channel)
    paths: tuple[str, ...] = tuple(
        ".".join(part.path)
        for text in (subject, body)
        for part in parse_template(text)
        if part.literal is None
    )
    occurrences: list[tuple[str, int]] = []
    for path, field_name in VARIABLE_FIELDS:
        count: int = paths.count(path)
        if count:
            occurrences.append((field_name, count))
    return tuple(occurrences)


def render_for_channel(
    texts: TemplateTexts,
    channel: NotificationChannel,
    context: NotificationContext,
    event: NotificationEvent,
) -> tuple[str, str]:
    """Render the channel variant's subject and body.

    Only variables allowed for the event are substituted. Subject newlines become
    spaces, Slack values (never literal text) are escaped, and the body is
    right-stripped so an empty optional last line disappears.
    """
    subject_text: str
    body_text: str
    subject_text, body_text = channel_texts(texts, channel)
    allowed: tuple[str, ...] = allowed_paths(event)
    escape: bool = channel is NotificationChannel.SLACK
    subject: str = _render(parse_template(subject_text), context, allowed, escape)
    body: str = _render(parse_template(body_text), context, allowed, escape)
    return subject.replace("\r", " ").replace("\n", " "), body.rstrip()


def _render(
    template: Template, context: NotificationContext, allowed: tuple[str, ...], escape: bool
) -> str:
    """Join literal parts with substituted allow-listed values."""
    pieces: list[str] = []
    for part in template:
        if part.literal is not None:
            pieces.append(part.literal)
            continue
        path: str = ".".join(part.path)
        value: str = lookup(context, path) if path in allowed else ""
        pieces.append(_slack_escape(value) if escape else value)
    return "".join(pieces)


def _slack_escape(value: str) -> str:
    """Escape Slack control characters so values cannot form mentions or links."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
