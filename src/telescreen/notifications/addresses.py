"""Channel addresses of resolved notification recipients."""

from telescreen.core.vocabulary import NotificationChannel
from telescreen.ports.directory import ResolvedRecipients


def channel_addresses(
    recipients: ResolvedRecipients, channel: NotificationChannel
) -> tuple[str, ...]:
    """Return the delivery endpoints of resolved recipients on one channel."""
    if channel is NotificationChannel.SLACK:
        return recipients.slack_channels + recipients.slack_user_ids
    return recipients.emails
