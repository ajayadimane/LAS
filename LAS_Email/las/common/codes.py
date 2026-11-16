"""Public IDs, deterministic normalisation of mobiles/emails/amounts (used by parse and batch)."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

MOBILE_RE = re.compile(r"^[6-9]\d{9}$")
CUSTOMER_CODE_RE = re.compile(r"\bLC\d{6}\b", re.IGNORECASE)
CASE_CODE_RE = re.compile(r"\bLAS-\d{4}-\d{6}\b", re.IGNORECASE)
SR_CODE_RE = re.compile(r"\bSR-\d{4}-\d{6}\b", re.IGNORECASE)
INVOICE_NO_RE = re.compile(r"\bINV-\d{4}-\d{6}\b", re.IGNORECASE)
BATCH_CODE_RE = re.compile(r"\bBATCH-\d{4}-\d{5}\b", re.IGNORECASE)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

CODE_PATTERNS: dict[str, re.Pattern[str]] = {
    "customer": CUSTOMER_CODE_RE,
    "case": CASE_CODE_RE,
    "sr": SR_CODE_RE,
    "invoice": INVOICE_NO_RE,
    "batch": BATCH_CODE_RE,
}


def ascii_digits(text: str) -> str:
    """Map Devanagari, Kannada and other Unicode decimal digits to ASCII 0-9."""
    out = []
    for ch in text:
        if ch.isdigit() and not ("0" <= ch <= "9"):
            try:
                out.append(str(unicodedata.decimal(ch)))
                continue
            except ValueError:
                pass
        out.append(ch)
    return "".join(out)


def normalise_mobile(value: str | None) -> str | None:
    """'+91 98765-43210' / '098765 43210' / '९८७६५४३२१०' -> '9876543210'; None if invalid."""
    if not value:
        return None
    digits = re.sub(r"\D", "", ascii_digits(value))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if MOBILE_RE.match(digits) else None


def normalise_email(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().strip("<>").lower()
    return v if EMAIL_RE.fullmatch(v) else None


def normalise_code(value: str) -> str:
    return value.strip().upper()


def parse_amount(value: str | int | float | Decimal | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"))
    s = ascii_digits(str(value)).replace(",", "").replace("₹", "").strip()
    s = re.sub(r"(?i)^(rs\.?|inr)\s*", "", s)
    s = re.sub(r"(?i)\s*(/-|rs\.?|rupees)$", "", s)
    try:
        return Decimal(s).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def parse_number(value: str | None) -> Decimal | None:
    """First plain number in a unit-suffixed string, e.g. '35 HP' / '4.5 acres' / '82%' -> Decimal.

    Unlike ``parse_amount``, this doesn't strip currency markers -- it's for non-money figures
    (horsepower, land area, utilisation percentages) where the unit is free text around a number.
    """
    if not value:
        return None
    m = _NUMBER_RE.search(ascii_digits(value))
    if not m:
        return None
    try:
        return Decimal(m.group(0).replace(",", ""))
    except InvalidOperation:
        return None
