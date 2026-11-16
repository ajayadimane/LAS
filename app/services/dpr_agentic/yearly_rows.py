"""Reads a DPR's yearly DSCR-statement rows in code, when LiteParse gives
them as clean "label  v1  v2 ... vN" lines.

Why: the debt-service rows decide the DSCR, and Gemini read them
differently between runs of the same DPR -- seen live: one run copied the
"TL Installment" row into principal, another missed it, and principal vs
full instalment changes the DSCR from 3.28 to 2.27. When the rows are
printed one per line, reading them needs no judgement.

Four rows on pages tagged dscr / repayment_schedule, matched by label: the
full instalment (principal + interest, "TL Installment", "EMI"), principal
repaid, interest on the term loan, and the stated DSCR. Two rows on pages
tagged balance_sheet: total assets and total liabilities (for the check
that the projected balance sheet balances -- seen live: a DPR whose assets
and liabilities differ by 30 lakh in every year, which nothing caught).
A row printed twice with different values is dropped (ambiguous); a
trailing total column (last value = sum of the others) is ignored; rows
whose year count differs from the page's common count are dropped. The
first value is year 1, as everywhere else.

Pure functions; no LLM, no I/O.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .context_router import page_sections

# Order matters: the first matching pattern names the row.
_LABELS: tuple[tuple[str, re.Pattern], ...] = (
    (
        "stated_dscr",
        re.compile(r"\bd\.?\s*s\.?\s*c\.?\s*r\b|debt\s+service\s+coverage", re.I),
    ),
    ("principal_repayment", re.compile(r"principal", re.I)),
    ("total_instalment", re.compile(r"instal+ment|\bemi\b", re.I)),
    (
        "principal_repayment",
        re.compile(
            r"repayment\s+of\s+(?:the\s+)?(?:term\s+)?loan|loan\s+repayment|"
            r"repayment\s+of\s+tl",
            re.I,
        ),
    ),
    (
        "interest_on_term_loan",
        re.compile(r"interest\s+on\s+(?:the\s+)?(?:term\s+loan|tl)\b", re.I),
    ),
)
# Balance-sheet totals: the row must start with "Total"; "Current assets",
# "Total current liabilities" and the like are not the totals.
_BALANCE_SHEET_LABELS: tuple[tuple[str, re.Pattern], ...] = (
    ("total_assets", re.compile(r"^\s*total\s+assets?\b(?!.*current)", re.I)),
    (
        "total_liabilities",
        re.compile(r"^\s*total\s+liabilit\w*(?:\s*(?:&|and)\s*capital)?\s*$", re.I),
    ),
)
_EXCLUDE = re.compile(r"average|avg|cumulative|subsid", re.I)
_NUMBER = re.compile(r"^\(?-?\d[\d,]*(?:\.\d+)?\)?$|^-$")
MIN_YEARS = 3


@dataclass(frozen=True)
class YearlyRow:
    field: str
    values: tuple[float, ...]
    page: int


def _parse_number(token: str) -> float:
    if token == "-":
        return 0.0
    negative = token.startswith("(") and token.endswith(")")
    value = float(token.strip("()").replace(",", ""))
    return -value if negative else value


def _split(line: str) -> tuple[str, list[float]] | None:
    tokens = line.split()
    values: list[str] = []
    while tokens and _NUMBER.match(tokens[-1]):
        values.insert(0, tokens.pop())
    label = " ".join(tokens)
    if not re.search(r"[A-Za-z]", label) or len(values) < MIN_YEARS:
        return None
    return label, [_parse_number(v) for v in values]


def _drop_total_column(values: list[float]) -> list[float]:
    if len(values) > MIN_YEARS and abs(sum(values[:-1]) - values[-1]) <= 0.02 * max(
        1.0, abs(values[-1])
    ):
        return values[:-1]
    return values


def parse_yearly_rows(pages: list) -> dict[str, YearlyRow]:
    """{field: YearlyRow} for the DSCR-statement rows found unambiguously."""
    found: dict[str, set[YearlyRow]] = {}
    for page in pages:
        sections = page_sections(page.text)
        labels: tuple[tuple[str, re.Pattern], ...] = ()
        if sections & {"dscr", "repayment_schedule"}:
            labels += _LABELS
        if "balance_sheet" in sections:
            labels += _BALANCE_SHEET_LABELS
        if not labels:
            continue
        rows: list[tuple[str, list[float]]] = []
        for line in page.text.splitlines():
            split = _split(line)
            if split is None or _EXCLUDE.search(split[0]):
                continue
            field = next(
                (f for f, pattern in labels if pattern.search(split[0])), None
            )
            if field is not None:
                rows.append((field, _drop_total_column(split[1])))
        if not rows:
            continue
        common = Counter(len(v) for _, v in rows).most_common(1)[0][0]
        for field, values in rows:
            if len(values) == common:
                found.setdefault(field, set()).add(
                    YearlyRow(field, tuple(values), page.page)
                )
    out = {}
    for field, candidates in found.items():
        if len({c.values for c in candidates}) == 1:
            out[field] = min(candidates, key=lambda c: c.page)
    return out


# A yearly money row whose largest value is at least this is printed in
# rupees, not lakh: Rs 5,000 lakh (Rs 50 crore) a year is beyond any DPR in
# scope, while a rupee instalment is always far above it. Seen live: a DSCR
# page printing "Total instalment 4,06,680" read as 406680 lakh, giving a
# DSCR of 0.0 and a false must-fix.
RUPEE_ROW_THRESHOLD = 5000
_MONEY_FIELDS = (
    "total_instalment",
    "principal_repayment",
    "interest_on_term_loan",
    "total_assets",
    "total_liabilities",
)


def _in_lakh(row: YearlyRow) -> YearlyRow:
    if row.field in _MONEY_FIELDS and max(abs(v) for v in row.values) >= (
        RUPEE_ROW_THRESHOLD
    ):
        return YearlyRow(
            row.field, tuple(round(v / 100_000, 2) for v in row.values), row.page
        )
    return row


def apply_yearly_rows(fin, rows: dict[str, YearlyRow]):
    """Code-read rows replace the model's values for the same years (they
    are the printed figures; the model's reading varied between runs). A
    principal the model copied from what is printed as the full instalment
    is cleared when no separate principal row exists. Returns a copy."""
    from .financial_schema import YearFinancials

    if not rows:
        return fin
    rows = {field: _in_lakh(row) for field, row in rows.items()}
    instalment, principal = (
        rows.get("total_instalment"),
        rows.get("principal_repayment"),
    )
    if instalment and principal and instalment.values == principal.values:
        # An "instalment" row equal to the principal row is principal only
        # (seen live: "Total instalment" on one page = "Principal" on
        # another); as the whole debt service it would drop interest.
        rows = {k: v for k, v in rows.items() if k != "total_instalment"}
    fixed = fin.model_copy(deep=True)
    by_year = {y.year: y for y in fixed.years}
    if "total_instalment" in rows and "principal_repayment" not in rows:
        # The DPR prints its debt service as one instalment row and no
        # principal row, so any principal the model returned is either that
        # instalment mislabelled or its own arithmetic (seen live:
        # instalment minus interest, despite the prompt) -- never printed.
        for y in fixed.years:
            y.principal_repayment = None
    for field, row in rows.items():
        for i, value in enumerate(row.values, start=1):
            if i not in by_year:
                by_year[i] = YearFinancials(year=i)
                fixed.years.append(by_year[i])
            setattr(by_year[i], field, value)
    fixed.years.sort(key=lambda y: y.year)
    return fixed
