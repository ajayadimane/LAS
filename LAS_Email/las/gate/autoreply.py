"""Detect auto-generated mail (RFC 3834 + common vendor headers). Such mail never gets a reply."""

from __future__ import annotations

from las.ingest.mime import ParsedMail

_PRECEDENCE = {"bulk", "auto_reply", "junk", "list"}
_SYSTEM_LOCALPARTS = {
    "mailer-daemon",
    "postmaster",
    "no-reply",
    "noreply",
    "do-not-reply",
    "donotreply",
    "bounce",
    "bounces",
}


def is_auto_generated(
    mail: ParsedMail, *, own_addresses: set[str] | frozenset[str] = frozenset()
) -> bool:
    auto = (mail.header("Auto-Submitted") or "").strip().lower()
    if auto and auto != "no":
        return True
    if (
        mail.header("X-Autoreply")
        or mail.header("X-Autorespond")
        or mail.header("X-Auto-Response-Suppress") in ("All", "OOF")
    ):
        return True
    if (mail.header("Precedence") or "").strip().lower() in _PRECEDENCE:
        return True
    if mail.header("List-Id") or mail.header("List-Unsubscribe"):
        return True
    if (mail.header("Return-Path") or "").strip() == "<>":
        return True  # bounce / DSN
    ctype = mail.message.get_content_type()
    if ctype == "multipart/report":
        return True
    local = mail.from_address.split("@", 1)[0]
    if local in _SYSTEM_LOCALPARTS:
        return True
    return mail.from_address in own_addresses  # our own mail looping back
