"""Outgoing mail over SMTP (Zoho Mail).

Uses the standard library's smtplib in a worker thread: one message per
customer registration does not justify an async SMTP dependency. TLS is
always verified. Sending is never retried here: a failed send is reported to
the caller, which decides what the BD sees.
"""

import asyncio
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class MailNotConfiguredError(RuntimeError):
    """No SMTP password in settings, so nothing can be sent."""


class MailSendError(RuntimeError):
    """The SMTP server refused the message or could not be reached."""


@dataclass(frozen=True)
class Mail:
    to: str
    subject: str
    text: str
    html: str


class Mailer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def configured(self) -> bool:
        return self._settings.smtp_password is not None and bool(
            self._settings.smtp_password.get_secret_value()
        )

    async def send(self, mail: Mail) -> None:
        if not self.configured:
            raise MailNotConfiguredError("SMTP_PASSWORD is not set")
        message = self._build(mail)
        try:
            await asyncio.to_thread(self._send_sync, message)
        except (smtplib.SMTPException, OSError) as exc:
            # The exception text can name the server's reply, never our password.
            logger.warning(
                "mail_failed to_domain=%s error=%s",
                _domain(mail.to),
                type(exc).__name__,
            )
            raise MailSendError(type(exc).__name__) from exc
        logger.info("mail_sent to_domain=%s subject=%r", _domain(mail.to), mail.subject)

    def _build(self, mail: Mail) -> EmailMessage:
        s = self._settings
        message = EmailMessage()
        message["From"] = formataddr((s.mail_from_name, s.mail_from))
        message["To"] = mail.to
        message["Subject"] = mail.subject
        message["Message-ID"] = make_msgid(domain=_domain(s.mail_from) or None)
        message.set_content(mail.text)
        message.add_alternative(mail.html, subtype="html")
        return message

    def _send_sync(self, message: EmailMessage) -> None:
        s = self._settings
        assert s.smtp_password is not None
        context = ssl.create_default_context()
        user = s.smtp_username or s.mail_from
        implicit_tls = s.smtp_port == 465
        if implicit_tls:
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                s.smtp_host,
                s.smtp_port,
                timeout=s.smtp_timeout_seconds,
                context=context,
            )
        else:
            server = smtplib.SMTP(
                s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds
            )
        with server:
            if not implicit_tls:
                server.starttls(context=context)
            server.login(user, s.smtp_password.get_secret_value())
            server.send_message(message)


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1] if "@" in address else ""
