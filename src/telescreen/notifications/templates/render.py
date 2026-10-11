"""Channel-specific rendering of template text from an allow-listed context."""

from telescreen.core.vocabulary import NotificationChannel, NotificationEvent
from telescreen.notifications.templates.model import TemplateTexts
from telescreen.notifications.templates.variables import allowed_paths, lookup
from telescreen.ports.notifications import NotificationContext
from telescreen.workflow.dsl.model import Template
from telescreen.workflow.dsl.templates import parse_template


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
    subject_text: str = texts.subject
    body_text: str = texts.body
    if channel is NotificationChannel.EMAIL:
        if texts.email_subject is not None:
            subject_text = texts.email_subject
        if texts.email_body is not None:
            body_text = texts.email_body
    elif texts.slack_body is not None:
        body_text = texts.slack_body
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
