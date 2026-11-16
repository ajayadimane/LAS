"""Deterministic candidate pre-extraction. The LLM may only assign roles to these values."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from las.common.codes import (
    CODE_PATTERNS,
    EMAIL_RE,
    ascii_digits,
    normalise_email,
    normalise_mobile,
    parse_amount,
)

# 10-digit mobile with optional +91 / 91 / 0 prefix and space/dash/dot separators.
_MOBILE = re.compile(r"(?<![\d])(?:\+?91[\s.-]?|0)?[6-9](?:[\s.-]?\d){9}(?![\d])")
_UTR12 = re.compile(r"(?<![\d])\d{12}(?![\d])")
_TXN_KEYWORD = re.compile(
    r"(?i)(?:utr|rrn|txn|transaction|trans\.?|ref(?:erence)?|payment)\s*(?:no\.?|number|id|#)?\s*[:#-]?\s*"
    r"([A-Za-z0-9]{8,24})"
)
_AMOUNT_PREFIX = re.compile(r"(?i)(?:rs\.?|inr|₹)[ \t]*([\d,]+(?:\.\d{1,2})?)")
_AMOUNT_SUFFIX = re.compile(r"(?i)(?<![\w.])([\d,]+(?:\.\d{1,2})?)[ \t]*(?:/-|rs\b|rupees|रुपये|ರೂ)")


@dataclass
class Candidates:
    mobiles: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    txn_refs: list[str] = field(default_factory=list)
    amounts: list[Decimal] = field(default_factory=list)
    codes: dict[str, list[str]] = field(default_factory=dict)

    def as_prompt(self) -> str:
        codes = "; ".join(f"{k}: {', '.join(v)}" for k, v in self.codes.items() if v) or "none"
        return (
            f"mobiles: {', '.join(self.mobiles) or 'none'}\n"
            f"emails: {', '.join(self.emails) or 'none'}\n"
            f"txn_refs: {', '.join(self.txn_refs) or 'none'}\n"
            f"amounts: {', '.join(str(a) for a in self.amounts) or 'none'}\n"
            f"our_codes: {codes}"
        )

    def all_codes(self) -> set[str]:
        return {c for v in self.codes.values() for c in v}


def _uniq(items: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for i in items:
        seen.setdefault(i, None)
    return list(seen)


def extract_candidates(text: str, *, own_addresses: set[str] | None = None) -> Candidates:
    t = ascii_digits(text)
    mobiles = [m for m in (normalise_mobile(x.group(0)) for x in _MOBILE.finditer(t)) if m]
    own = {a.lower() for a in (own_addresses or set())}
    emails = [
        e for e in (normalise_email(x.group(0)) for x in EMAIL_RE.finditer(t)) if e and e not in own
    ]
    txn: list[str] = []
    for m in _TXN_KEYWORD.finditer(t):
        val = m.group(1)
        if any(ch.isdigit() for ch in val) and not normalise_mobile(val):
            txn.append(val.upper())
    for m in _UTR12.finditer(t):
        val = m.group(0)
        # 91XXXXXXXXXX is a mobile with country code, not a UTR — unless labelled as one.
        if normalise_mobile(val) is None or val in txn:
            txn.append(val)
    amounts: list[Decimal] = []
    for rx in (_AMOUNT_PREFIX, _AMOUNT_SUFFIX):
        for m in rx.finditer(t):
            a = parse_amount(m.group(1))
            if a is not None and a > 0 and a not in amounts:
                amounts.append(a)
    codes = {
        k: _uniq([c.group(0).upper() for c in rx.finditer(t)]) for k, rx in CODE_PATTERNS.items()
    }
    return Candidates(
        mobiles=_uniq(mobiles),
        emails=_uniq(emails),
        txn_refs=_uniq(txn),
        amounts=amounts,
        codes=codes,
    )
