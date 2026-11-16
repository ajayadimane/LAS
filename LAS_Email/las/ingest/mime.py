"""Parse RFC 5322 headers and the human-readable body of an inbound mail."""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import re
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr, parsedate_to_datetime


@dataclass
class ParsedMail:
    message: EmailMessage
    message_id: str
    in_reply_to: str | None
    references: list[str]
    from_address: str
    from_display: str | None
    from_count: int
    to: list[str]
    cc: list[str]
    subject: str
    date: dt.datetime | None
    body_text: str
    headers: dict[str, list[str]] = field(default_factory=dict)

    def header(self, name: str) -> str | None:
        values = self.headers.get(name.lower())
        return values[0] if values else None

    def all_headers(self, name: str) -> list[str]:
        return self.headers.get(name.lower(), [])


_MSGID = re.compile(r"<[^<>\s]+>")
_TAG = re.compile(r"<[^>]+>")
_BLOCK = re.compile(r"(?i)</?(p|div|br|tr|li|h\d|table)[^>]*>")


def html_to_text(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?</\1>", "", value)
    value = re.sub(r"(?is)<blockquote[^>]*>", "\n> ", value)
    value = _BLOCK.sub("\n", value)
    value = _TAG.sub("", value)
    value = html.unescape(value)
    lines = [ln.rstrip() for ln in value.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _msg_ids(value: str | None) -> list[str]:
    return _MSGID.findall(value or "")


def extract_body(msg: EmailMessage) -> str:
    """Top-level text/plain (or text/html converted) excluding attachments and nested mails."""
    plain: str | None = None
    html_body: str | None = None
    for part in msg.walk():
        if part is not msg and part.get_content_maintype() == "message":
            break  # don't descend into attached/forwarded .eml for the body
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        try:
            content = part.get_content()
        except (LookupError, UnicodeDecodeError):
            payload = part.get_payload(decode=True)
            content = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else ""
        if ctype == "text/plain" and plain is None:
            plain = str(content)
        elif ctype == "text/html" and html_body is None:
            html_body = str(content)
    if plain and plain.strip():
        return plain.replace("\r\n", "\n").strip()
    if html_body:
        return html_to_text(html_body)
    return ""


def parse_mail(raw: bytes) -> ParsedMail:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    assert isinstance(msg, EmailMessage)
    headers: dict[str, list[str]] = {}
    for k, v in msg.raw_items():
        headers.setdefault(k.lower(), []).append(str(v))
    msg_ids = _msg_ids(msg.get("Message-ID"))
    message_id = (
        msg_ids[0] if msg_ids else f"<{hashlib.sha256(raw).hexdigest()}@missing-message-id>"
    )
    from_values = headers.get("from", [])
    from_pairs = getaddresses(from_values)
    display, addr = parseaddr(from_values[0]) if from_values else ("", "")
    date: dt.datetime | None
    try:
        date = parsedate_to_datetime(msg["Date"]) if msg["Date"] else None
        if date is not None and date.tzinfo is None:
            date = date.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        date = None
    irt = _msg_ids(msg.get("In-Reply-To"))
    return ParsedMail(
        message=msg,
        message_id=message_id,
        in_reply_to=irt[0] if irt else None,
        references=_msg_ids(msg.get("References")),
        from_address=addr.strip().lower(),
        from_display=display or None,
        from_count=len([a for _, a in from_pairs if a]),
        to=[a.lower() for _, a in getaddresses(headers.get("to", [])) if a],
        cc=[a.lower() for _, a in getaddresses(headers.get("cc", [])) if a],
        subject=str(msg.get("Subject", "") or ""),
        date=date,
        body_text=extract_body(msg),
        headers=headers,
    )
