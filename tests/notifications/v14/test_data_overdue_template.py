"""Safe data overdue built-in notification template tests."""

from telescreen.core.vocabulary import NotificationChannel, NotificationEvent
from telescreen.masking.redact import Redactor
from telescreen.notifications.templates.builtins import builtin_for
from telescreen.notifications.templates.model import BuiltinTemplate
from telescreen.notifications.templates.render import render_for_channel
from telescreen.notifications.templates.synthetic import synthetic_context
from telescreen.notifications.templates.validate import validate_template
from telescreen.notifications.templates.variables import allowed_paths


def test_data_overdue_template_paths_validate_and_render() -> None:
    """Allow only dotted data paths and render all synthetic values."""
    event: NotificationEvent = NotificationEvent.DATA_OVERDUE
    template: BuiltinTemplate = builtin_for(event)
    assert allowed_paths(event) == (
        "data.short_id",
        "data.name",
        "data.locator",
        "data.deletion_due",
        "data.link",
    )
    assert validate_template(template.name, event, template.texts, None, Redactor.default()) == ()
    rendered: tuple[str, str] = render_for_channel(
        template.texts, NotificationChannel.EMAIL, synthetic_context(event), event
    )
    subject: str
    body: str
    subject, body = rendered
    assert "{{" not in subject and "{{" not in body
