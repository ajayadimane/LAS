"""Deterministic reader for a per-instalment loan repayment schedule.

Many DPRs print the term-loan schedule month by month (or quarter / half
year): one row per period with opening balance, instalment, interest,
principal and closing balance. Asking Gemini to transcribe or total such a
table is exactly the arithmetic-heavy, long-output work it is bad at (seen
live: an 84-row monthly schedule exhausted 4k and then 8k output tokens of
"thinking" and never produced an answer). The rows are regular enough to
read in code, and every row checks itself:

    closing ~= opening - principal           (balance roll-forward)
    instalment ~= interest + principal       (when an instalment was paid)

A table is only accepted when nearly all of its rows pass both checks, so a
mis-parsed layout yields nothing rather than wrong numbers -- the caller
then falls back to Gemini's own extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .financial_schema import RepaymentInstalment

_PERIOD = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[-' ]?\d{2,4}"
_ROW = re.compile(rf"^\s*({_PERIOD})\s+(.+?)\s*$", re.IGNORECASE)
_NUMBER = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?$|^-$")
_YEAR_HEADER = re.compile(r"\bYear\s*[-:]?\s*([IVXLC]+|\d{1,2})\b", re.IGNORECASE)
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
_MONTHS = (
    "jan",
    "feb",
    "mar",
    "apr",
    "may",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
)

# Rupee rounding in the DPR's own table.
ROW_TOLERANCE = 1.0
MIN_ROWS = 12
MIN_CONSISTENT_SHARE = 0.95


@dataclass(frozen=True)
class ParsedSchedule:
    instalments: list[RepaymentInstalment]
    unit: str  # "rupee" or "lakh"
    pages: list[int]


def _roman(token: str) -> int | None:
    token = token.upper()
    if token.isdigit():
        return int(token)
    if any(c not in _ROMAN for c in token):
        return None
    total = 0
    for i, c in enumerate(token):
        value = _ROMAN[c]
        nxt = _ROMAN[token[i + 1]] if i + 1 < len(token) else 0
        total += -value if value < nxt else value
    return total


def _amount(token: str) -> float:
    if token == "-":
        return 0.0
    negative = token.startswith("(") or token.startswith("-")
    value = float(token.strip("()-").replace(",", ""))
    return -value if negative else value


def _fiscal_year_index(period: str, first_fy: list[int]) -> int:
    """1-based Indian fiscal year (April-March) counted from the first row's
    fiscal year; used only when the table has no "Year - N" headers."""
    month = _MONTHS.index(period[:3].lower()) + 1
    yy = int(re.findall(r"\d{2,4}", period)[-1][-2:])
    fy = yy if month >= 4 else yy - 1
    if not first_fy:
        first_fy.append(fy)
    return fy - first_fy[0] + 1


def _consistent(
    opening: float, instalment: float, interest: float, principal: float, closing: float
) -> bool:
    if opening == closing == interest == principal == instalment == 0:
        return True  # a not-yet-disbursed period printed as dashes
    if abs((opening - principal) - closing) > ROW_TOLERANCE:
        return False
    if instalment and abs((interest + principal) - instalment) > ROW_TOLERANCE:
        return False
    return True


def parse_instalment_schedule(pages: list) -> ParsedSchedule | None:
    """`pages`: objects with `.page` and `.text`. Returns the schedule's
    rows (zero-amount pre-disbursement rows dropped) or None."""
    rows: list[RepaymentInstalment] = []
    checks: list[bool] = []
    source_pages: set[int] = set()
    first_fy: list[int] = []
    # Carried across pages: a year's rows can continue onto the next page
    # before (or without) a new "Year - N" header.
    loan_year: int | None = None
    for page in sorted(pages, key=lambda p: p.page):
        for line in page.text.splitlines():
            header = _YEAR_HEADER.search(line)
            if header and not _ROW.match(line):
                loan_year = _roman(header.group(1)) or loan_year
                continue
            match = _ROW.match(line)
            if not match:
                continue
            tokens = match.group(2).split()
            if len(tokens) != 5 or not all(_NUMBER.match(t) for t in tokens):
                continue
            opening, instalment, interest, principal, closing = map(_amount, tokens)
            checks.append(
                _consistent(opening, instalment, interest, principal, closing)
            )
            if not any((opening, instalment, interest, principal, closing)):
                continue
            period = match.group(1)
            year = loan_year or _fiscal_year_index(period, first_fy)
            rows.append(
                RepaymentInstalment(
                    loan_year=year,
                    period=period,
                    instalment=instalment or None,
                    interest=interest,
                    principal=principal,
                    page=page.page,
                )
            )
            source_pages.add(page.page)
    if len(checks) < MIN_ROWS or sum(checks) / len(checks) < MIN_CONSISTENT_SHARE:
        return None
    if not rows:
        return None
    # A balance of 1,000+ is rupees: a term loan stated in lakh would need to
    # exceed Rs 10 crore to print that large, far beyond this product.
    biggest = max(abs(r.principal or 0) for r in rows)
    unit = (
        "rupee"
        if biggest >= 1_000 or max(abs(r.interest or 0) for r in rows) >= 1_000
        else "lakh"
    )
    return ParsedSchedule(instalments=rows, unit=unit, pages=sorted(source_pages))
