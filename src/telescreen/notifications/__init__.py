"""Outbound notifications protected by a data-loss-prevention gate."""

from telescreen.notifications.delivery import NotificationDeliveryHandler
from telescreen.notifications.dlp import CensoredMessage, DlpGate, OutboundMessage, rehydrate
from telescreen.notifications.email import EmailNotifier, SmtplibTransport, SmtpTransport
from telescreen.notifications.repository import NotificationRepository
from telescreen.notifications.sender import NotificationService
from telescreen.notifications.slack import SlackNotifier

__all__: list[str] = [
    "CensoredMessage",
    "DlpGate",
    "EmailNotifier",
    "NotificationDeliveryHandler",
    "NotificationRepository",
    "NotificationService",
    "OutboundMessage",
    "SlackNotifier",
    "SmtpTransport",
    "SmtplibTransport",
    "rehydrate",
]
