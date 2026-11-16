"""PII detection and masking (mobile, email, PAN, Aadhaar)."""

from __future__ import annotations

import re
from typing import Any

# Aadhaar: 12 digits, optionally grouped 4-4-4, first digit 2-9.
_AADHAAR = re.compile(r"(?<!\d)[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")
# Indian mobile, optional +91 / 0 prefix and separators.
_MOBILE = re.compile(r"(?<!\d)(?:\+?91[\s-]?|0)?[6-9](?:[\s-]?\d){9}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b", re.IGNORECASE)


def mask_mobile(mobile: str) -> str:
    digits = re.sub(r"\D", "", mobile)
    if len(digits) < 6:
        return "x" * len(digits)
    return (
        f"{digits[:2]}xxxx{digits[-4:]}" if len(digits) == 10 else f"{digits[:2]}xxxx{digits[-2:]}"
    )


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def _mask_digits(m: re.Match[str]) -> str:
    raw = m.group(0)
    digits = re.sub(r"\D", "", raw)
    return "xxxxxxxx" + digits[-4:]


def mask_text(value: str) -> str:
    value = _EMAIL.sub(lambda m: mask_email(m.group(0)), value)
    value = _PAN.sub(lambda m: m.group(0)[:2].upper() + "xxxxxxx" + m.group(0)[-1:].upper(), value)
    value = _AADHAAR.sub(_mask_digits, value)
    value = _MOBILE.sub(lambda m: mask_mobile(m.group(0)[-10:]), value)
    return value


def mask_any(value: Any) -> Any:
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return {k: mask_any(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(mask_any(v) for v in value)
    return value
