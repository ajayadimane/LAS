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
    # Threading, for mail a reply is expected to (email intake). A reply's
    # In-Reply-To / References then name one of our Message-IDs.
    in_reply_to: str | None = None
    references: tuple[str, ...] = ()
    # Marks the mail as automated (RFC 3834), so the recipient's
    # out-of-office reply is not sent back to us.
    auto_submitted: bool = False


class Mailer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def configured(self) -> bool:
        return self._settings.smtp_password is not None and bool(
            self._settings.smtp_password.get_secret_value()
        )

    async def send(self, mail: Mail) -> str:
        """Send it; returns the Message-ID it was sent with."""
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
        return str(message["Message-ID"])

    def _build(self, mail: Mail) -> EmailMessage:
        s = self._settings
        message = EmailMessage()
        message["From"] = formataddr((s.mail_from_name, s.mail_from))
        message["To"] = mail.to
        message["Subject"] = mail.subject
        message["Message-ID"] = make_msgid(domain=_domain(s.mail_from) or None)
        if mail.in_reply_to:
            message["In-Reply-To"] = mail.in_reply_to
        refs = [r for r in mail.references if r]
        if mail.in_reply_to and mail.in_reply_to not in refs:
            refs.append(mail.in_reply_to)
        if refs:
            message["References"] = " ".join(refs[-20:])
        if mail.auto_submitted:
            message["Auto-Submitted"] = "auto-replied"
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
