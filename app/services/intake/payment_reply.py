"""Reading the BD's Pay now / Pay later answer from their reply.

Deterministic on purpose -- this decides whether money moves:

1. The buttons in our "registered" mail open a reply whose subject is
   ``LAS-PAY <ref> PAY_NOW`` or ``LAS-PAY <ref> PAY_LATER``. The ref must be
   this case's.
2. Otherwise the BD's own new text (quoted history cut off) must say exactly
   one of "pay now" / "pay later". Both, or neither, is no answer: we ask again.

The LLM's reading of the mail is not used here.
"""

import re

from app.services.intake.mail import PAY_LATER, PAY_NOW

_SUBJECT = re.compile(r"LAS-PAY\s+([0-9A-F]{10})\s+(PAY_NOW|PAY_LATER)\b", re.I)
_NOW = re.compile(r"\bpay[\s_-]*now\b", re.I)
_LATER = re.compile(r"\bpay[\s_-]*later\b", re.I)
# Where a reply's quoted history starts, in the common clients.
_QUOTE_START = re.compile(
    r"^(>|On .{0,200}wrote:\s*$|-{2,}\s*Original Message|From:\s|Sent from|"
    r"={4,} Forwarded message)",
    re.I | re.M,
)


def new_text(body: str) -> str:
    """The reply's own words: everything before the quoted history."""
    match = _QUOTE_START.search(body or "")
    return (body or "")[: match.start()] if match else (body or "")


def payment_choice(subject: str, body: str, ref: str) -> str | None:
    """PAY_NOW, PAY_LATER, or None (no clear answer)."""
    for m in _SUBJECT.finditer(subject or ""):
        if m.group(1).upper() == ref.upper():
            return m.group(2).upper()
    text = new_text(body)
    now, later = bool(_NOW.search(text)), bool(_LATER.search(text))
    if now != later:
        return PAY_NOW if now else PAY_LATER
    return None


_PAID_SUBJECT = re.compile(r"LAS-PAID\s+([0-9A-F]{10})\b", re.I)
_PAID_TEXT = re.compile(r"\bcustomer\s+(has\s+)?paid\b", re.I)


def customer_paid(subject: str, body: str, ref: str) -> bool:
    """The BD confirms a Pay later customer has paid: the button's
    `LAS-PAID <ref>` subject, or "customer paid" in their own new words."""
    for m in _PAID_SUBJECT.finditer(subject or ""):
        if m.group(1).upper() == ref.upper():
            return True
    return bool(_PAID_TEXT.search(new_text(body)))
