"""Count producer-side redaction hits that apply to rendered message text."""

from telescreen.notifications.templates.model import TemplateTexts
from telescreen.notifications.templates.render import field_occurrences
from telescreen.ports.notification_types import ProducerRedactionHits
from telescreen.ports.notifications import NotificationRequest


def prior_hits_for(request: NotificationRequest, texts: TemplateTexts | None) -> int:
    """Fold only prior hits that can occur in the selected subject and body."""
    hits: ProducerRedactionHits | None = request.prior_hits
    if hits is None:
        return 0
    if request.event is None or request.inline:
        return hits.subject + hits.body
    if texts is None or request.context is None:
        return 0
    total: int = 0
    for name, count in field_occurrences(texts, request.channel):
        if getattr(request.context, name) is not None:
            total += count * next((hit.count for hit in hits.context if hit.field == name), 0)
    return total
