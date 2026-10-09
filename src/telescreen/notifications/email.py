"""SMTP transport and notifier for sealed notification content."""

import asyncio
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from telescreen.core.errors import TelescreenError
from telescreen.core.secrets import SecretValue
from telescreen.core.vocabulary import ErrorCode
from telescreen.notifications.dlp import CensoredMessage


class SmtpTransport(Protocol):
    """Synchronous SMTP boundary, executed away from the event loop."""

    def send(self, message: EmailMessage) -> None:
        """Deliver one fully formed email message."""
        ...


@dataclass(frozen=True)
class SmtplibTransport:
    """SMTP transport using stdlib SMTP and optional STARTTLS/authentication."""

    host: str
    port: int
    username: str | None
    password: SecretValue | None
    starttls: bool

    def send(self, message: EmailMessage) -> None:
        """Send an email via SMTP, raising only a static application error."""
        try:
            with smtplib.SMTP(self.host, self.port) as client:
                if self.starttls:
                    client.starttls()
                if self.username is not None and self.password is not None:
                    client.login(self.username, self.password.value)
                client.send_message(message)
        except (OSError, smtplib.SMTPException):
            raise TelescreenError(ErrorCode.DELIVERY_FAILED, "email delivery failed") from None


@dataclass(frozen=True)
class EmailNotifier:
    """Send sealed notification content through the configured SMTP transport."""

    transport: SmtpTransport
    sender: str

    async def send(self, to: str, message: CensoredMessage) -> None:
        """Build a text email and deliver it on a worker thread."""
        email_message: EmailMessage = EmailMessage()
        email_message["From"] = self.sender
        email_message["To"] = to
        email_message["Subject"] = message.subject
        email_message.set_content(message.body)
        try:
            await asyncio.to_thread(self.transport.send, email_message)
        except TelescreenError:
            raise
        except Exception:
            raise TelescreenError(ErrorCode.DELIVERY_FAILED, "email delivery failed") from None
