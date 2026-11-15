"""Email delivery. SMTP in production; a redacted console logger otherwise."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Protocol

from app.config import Settings

logger = logging.getLogger(__name__)


class EmailFailed(Exception):
    """The mail server could not be reached or refused the message."""


class Emailer(Protocol):
    def send(self, to: list[str], subject: str, body: str) -> None: ...


class ConsoleEmailer:
    """Logs that an email would be sent, without addresses or content."""

    def send(self, to: list[str], subject: str, body: str) -> None:
        logger.info("email (console backend): recipients=%d chars=%d", len(to), len(body))


class SmtpEmailer:
    """Sends email over SMTP with STARTTLS (for example Gmail with an app password)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def send(self, to: list[str], subject: str, body: str) -> None:
        if not to:
            return
        message = EmailMessage()
        message["From"] = self._settings.email_from
        message["To"] = self._settings.email_from
        message["Bcc"] = ", ".join(to)
        message["Subject"] = " ".join(subject.split())  # no header injection via newlines
        message.set_content(body)
        context = ssl.create_default_context()
        try:
            with smtplib.SMTP(
                self._settings.smtp_host, self._settings.smtp_port, timeout=15
            ) as smtp:
                smtp.starttls(context=context)
                smtp.login(self._settings.smtp_user, self._settings.smtp_password)
                smtp.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            # Log the error type only: server replies can echo addresses.
            logger.warning("email failed: %s", type(exc).__name__)
            raise EmailFailed from exc
        logger.info("email sent: recipients=%d", len(to))


def build_emailer(settings: Settings) -> Emailer:
    if settings.email_backend == "smtp" and settings.smtp_user and settings.smtp_password:
        return SmtpEmailer(settings)
    return ConsoleEmailer()
