"""Renders a 2.x CAMSnapshot as the management-approved "DPR Pre-submission
Review": a cover, four numbered sections (scheme fit, action plan, financial
corrections, verification and documents). The annexures A-G are not
printed; they stay available as JSON (cam_annexures.py).

Design reference: /NewCAMdesign/C1-C5 (*.dc.html, 794x1123 px artboards,
Inter, accent #1F6B4A). Rebuilt as flowing paged media rather than fixed
boards: the header and footer repeat on every page as running elements,
"Page N of M" counts real pages, and a long action plan continues on the
next page with its column headings repeated.

As in cam_render.py: templates only loop and substitute; every display value
is computed here; Jinja autoescape is on and nothing uses |safe on model
data. A value the snapshot does not have is printed "Pending" or "—", never
invented.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from .cam_snapshot import CAMSnapshot, Money, ReadinessStatus, ValueClassification
from .financial_ratios import STRESS_DSCR_FLOOR

PRODUCT_NAME = "LAS DPR Analyzer"
ACCENT = "#1F6B4A"

PRIORITY_ORDER = {"must_fix": 0, "should_fix": 1, "good_to_fix": 2}
PRIORITY_LABEL = {
    "must_fix": "Must fix",
    "should_fix": "Should fix",
    "good_to_fix": "Can improve",
}
PRIORITY_CHIP = {"must_fix": "must", "should_fix": "should", "good_to_fix": "imp"}

# "Why it matters" when the finding carries none of its own.
WHY_BY_CATEGORY = {
    "dscr": "Decides whether the bank sees the loan as repayable",
    "repayment": "Decides whether the bank sees the loan as repayable",
    "project_cost": "The bank fixes the loan amount on these figures",
    "financial_calculation": "Figures that do not add up get the DPR queried",
    "funding": "Changes how much the bank must lend",
    "interest_subvention": "Changes the interest and the repayment",
    "identity": "The bank checks every detail matches your documents",
    "inconsistency": "Contradictions make the bank query the DPR",
    "assumption": "Projections may look optimistic",
    "market": "Revenue may be overstated",
    "missing_document": "The bank will ask for it before it decides",
    "policy": "Scheme or bank conditions may not be met",
    "operations": "Running the project may cost more than shown",
    "risk": "Raises the bank's view of the risk",
}
DEFAULT_WHY = "The bank is likely to query it"

READINESS_OPTIONS = (
    ("ready", "Ready to submit"),
    ("minor", "Ready after minor fixes"),
    ("major", "Needs major revision"),
)
SCHEME_VERDICT = {
    "correct": "Correct scheme",
    "better_available": "Better scheme available",
    "not_eligible": "Scheme does not cover this project",
    "not_assessed": "Comparison pending",
}
ELIGIBLE_LABEL = {
    "likely": "Likely",
    "partly": "Partly",
    "no": "No",
    "not_assessed": "Not assessed",
}
MEASURE_STATUS = {
    "meets": "OK",
    "below": "Below what the bank wants",
    "pending": "Not enough information",
}
DOCUMENT_STATUS = {
    "available": "Available",
    "missing": "Missing",
    "update": "Update",
    "to_confirm": "To confirm",
}
KYC_STATUS = {
    "verified": "Verified",
    "mismatch": "Mismatch",
    "match": "Match",
    "differs": "Differs",
}


def _date(value: datetime | None) -> str | None:
    return value.strftime("%d-%m-%Y") if value else None


def _lakh(money: Money | None) -> str | None:
    if money is None:
        return None
    amount = money.amount
    if money.unit == "absolute":
        amount = amount / Decimal(100_000)
    elif money.unit == "crore":
        amount = amount * 100
    return f"₹ {amount:,.2f} lakh"


def reference(snapshot: CAMSnapshot) -> str:
    """The stored PSR reference; an unsaved preview prints its report id
    hash, as the 1.x layout did."""
    if snapshot.report_reference:
        return snapshot.report_reference
    from .cam_render import _public_report_reference

    return _public_report_reference(snapshot)


def _actionable(snapshot: CAMSnapshot) -> list:
    """The findings the customer must act on. Informational ones (severity
    info: e.g. one "DSCR YEAR n" per year, "no action needed from this
    figure alone") stay in the appendix register only: on 912a they filled
    a page of the to-do list."""
    return [f for f in snapshot.findings if f.severity.value != "info"]


def _counts(snapshot: CAMSnapshot) -> dict[str, int]:
    counts = dict.fromkeys(PRIORITY_ORDER, 0)
    for f in _actionable(snapshot):
        if f.priority.value in counts:
            counts[f.priority.value] += 1
    return counts


def readiness(snapshot: CAMSnapshot) -> dict:
    """The cover's readiness circle and option: any must-fix -> needs major
    revision; only should-fix -> ready after minor fixes; else ready. A
    report whose financial figures could not be read needs major revision."""
    counts = _counts(snapshot)
    ra = snapshot.readiness_assessment
    if counts["must_fix"] or ra.status == ReadinessStatus.INSUFFICIENT_INFORMATION:
        selected = "major"
    elif counts["should_fix"]:
        selected = "minor"
    else:
        selected = "ready"
    score = (
        f"{ra.total_score}%"
        if ra.score_mode == "checks" and ra.total_score is not None
        else "—"
    )
    note = None
    if ra.score_mode == "checks" and ra.checks_total:
        note = f"{ra.checks_passed} of {ra.checks_total} checks passed"
    if ra.status == ReadinessStatus.INSUFFICIENT_INFORMATION:
        note = "Your financial figures could not be read in full"
    return {
        "score": score,
        "note": note,
        "options": [
            {"label": label, "selected": key == selected}
            for key, label in READINESS_OPTIONS
        ],
    }


def _sector_kicker(snapshot: CAMSnapshot) -> str:
    agri = {"harvester", "polyhouse", "dairy", "cold_storage"}
    sector = " ".join(
        x
        for x in (
            snapshot.operational_assumptions.sector,
            snapshot.project_profile.business_sector,
        )
        if x
    ).casefold()
    is_agri = snapshot.operational_assumptions.sector in agri or any(
        w in sector for w in ("agri", "farm", "dairy", "horti", "crop", "harvest")
    )
    return "Agriculture term loan" if is_agri else "Term loan"


def build_cover(snapshot: CAMSnapshot) -> dict:
    p, b = snapshot.project_profile, snapshot.borrower_profile
    counts = _counts(snapshot)
    review = snapshot.scheme_review
    # "Best fit" only once a comparison recommended one (phase 3).
    best = review.recommended if review else None
    return {
        "kicker": f"Pre-submission review · {_sector_kicker(snapshot)}",
        "bank": p.lending_bank or "your bank's",
        "prepared_for": b.borrower_name or "Not yet confirmed",
        "project": ", ".join(x for x in (p.project_name, p.project_location) if x)
        or "Not stated in the DPR",
        "scheme": p.scheme_claimed or "Not stated in the DPR",
        "loan": _lakh(snapshot.means_of_finance.requested_loan) or "Not stated",
        "dpr_reviewed": f"{p.project_name or 'Detailed Project Report'}, "
        f"review version {snapshot.report_version}",
        "review_date": _date(snapshot.generated_at),
        "readiness": readiness(snapshot),
        "scheme_verdict": SCHEME_VERDICT[review.verdict if review else "not_assessed"],
        "scheme_chip": {
            "correct": "meets",
            "better_available": "should",
            "not_eligible": "must",
        }.get(review.verdict if review else "", "pend"),
        "best_fit": best.name if best else None,
        "counts": counts,
    }


def build_scheme_fit(snapshot: CAMSnapshot) -> dict:
    review = snapshot.scheme_review
    columns = []
    if review:
        for role, col in (
            ("Scheme you chose", review.chosen),
            ("Recommended", review.recommended),
            ("Alternative", review.alternative),
        ):
            columns.append({"role": role, "col": col, "rec": role == "Recommended"})
    rows = [
        ("You are eligible", "eligible"),
        ("Project cost ceiling", "cost_ceiling"),
        ("Your contribution needed", "contribution"),
        ("Capital subsidy", "capital_subsidy"),
        ("Interest subvention", "interest_subvention"),
        ("Credit guarantee", "credit_guarantee"),
        ("Tenure · moratorium", "tenure_moratorium"),
        ("Can be combined with", "combinable"),
    ]
    table = []
    for label, field in rows:
        cells = []
        for c in columns:
            col = c["col"]
            if col is None:
                value = "—"
            elif field == "eligible":
                value = ELIGIBLE_LABEL[col.eligible]
            else:
                value = getattr(col, field) or "—"
            cells.append(
                {
                    "value": value,
                    "rec": c["rec"],
                    "chip": field == "eligible" and col is not None,
                }
            )
        table.append({"label": label, "cells": cells})
    benefit = [
        {
            "value": (c["col"].total_benefit if c["col"] else None) or "—",
            "rec": c["rec"],
        }
        for c in columns
    ]
    return {
        "columns": [
            {
                "role": c["role"],
                "name": c["col"].name if c["col"] else "—",
                "rec": c["rec"],
            }
            for c in columns
        ],
        "rows": table,
        "benefit": benefit,
        "recommendation": review.recommendation if review else "",
        "gaps": [g.model_dump() for g in review.gaps] if review else [],
    }


# The action plan is read by the applicant. A calculated finding's own action
# is written for the bank ("Verify this figure with the applicant."), so the
# table says what the applicant should do instead.
CUSTOMER_ACTION_DEFAULT = (
    "Check this figure in your DPR and correct it, or add a note explaining "
    "why it differs from our calculation."
)
EXCERPT_MAX_CHARS = 240


def _plain_excerpt(text: str | None, max_chars: int = EXCERPT_MAX_CHARS) -> str:
    """`text` cut to whole sentences that fit `max_chars`; a first sentence
    longer than that is cut at a word boundary with an ellipsis."""
    text = " ".join((text or "").split())
    if len(text) <= max_chars:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept = ""
    for sentence in sentences:
        candidate = f"{kept} {sentence}".strip()
        if len(candidate) > max_chars:
            break
        kept = candidate
    if kept:
        return kept
    return text[:max_chars].rsplit(" ", 1)[0].rstrip(",;:") + "…"


# Banking terms the code-written findings use, explained in brackets the
# first time each appears in a row ("What we found", then "What to do").
PLAIN_TERMS: tuple[tuple[re.Pattern, str], ...] = (
    (
        re.compile(r"\bDSCR\b"),
        "DSCR (how many times your yearly earnings cover the loan instalments)",
    ),
    (re.compile(r"\btenor\b", re.I), "tenor (loan period)"),
    (
        re.compile(r"\bmoratorium\b", re.I),
        "moratorium (months before repayment starts)",
    ),
    (re.compile(r"\bP&L\b"), "P&L (profit and loss statement)"),
    (re.compile(r"\bOpEx\b"), "OpEx (running costs)"),
)

# Customer wording for recurring code-written findings whose own text is
# written for the bank: title -> (title, what we found, what to do).
CUSTOMER_TEXT: dict[str, tuple[str, str, str]] = {
    "Stated DSCR does not follow the standard formula": (
        "Your DSCR is worked out differently from the bank's way",
        "The bank works out DSCR as (profit + depreciation + interest) ÷ "
        "(loan principal + interest) for each year. The DSCR figures in your "
        "DPR do not follow this, so the bank will get different numbers.",
        "Work out DSCR again in your DPR for every year as (profit + "
        "depreciation + interest) ÷ (loan principal + interest).",
    ),
}


def _explain_terms(cells: list[str]) -> list[str]:
    """Adds the plain meaning to the first use of each term across `cells`."""
    out = list(cells)
    for pattern, explained in PLAIN_TERMS:
        for i, cell in enumerate(out):
            if pattern.search(cell):
                if explained not in cell:
                    out[i] = pattern.sub(explained, cell, count=1)
                break
    return out


def _is_calculated(f) -> bool:
    return f.value_classification == ValueClassification.RECOMPUTED


def _plain_title(f) -> str:
    """Code-built titles like "Means Of Finance Total does not reconcile"
    (a check name run through str.title()) in plain sentence case."""
    title = f.title.replace(" does not reconcile", " does not add up")
    # Seen live: the model wrote titles all in lower case.
    title = title[:1].upper() + title[1:]
    if _is_calculated(f):
        words = title.split(" ")
        # Lower-case the capitalised filler of "Means Of Finance Total".
        title = " ".join(
            [words[0]]
            + [
                w.lower() if w[:1].isupper() and w[1:].islower() else w
                for w in words[1:]
            ]
        )
    return title


def _found(f) -> str:
    if _is_calculated(f) and f.dpr_reported_value and f.recomputed_value:
        return (
            f"Your DPR: {f.dpr_reported_value.rstrip('.')}. "
            f"Should be: {f.recomputed_value.rstrip('.')}."
        )
    if f.explanation:
        return _plain_excerpt(f.explanation)
    return "Not found anywhere in your DPR."


def _customer_action(f) -> str:
    action = " ".join((f.suggested_action or "").split())
    if (
        not action
        or "applicant" in action.casefold()
        or action.startswith("No action needed")
    ):
        return CUSTOMER_ACTION_DEFAULT
    return _plain_excerpt(action)


def build_action_plan(snapshot: CAMSnapshot) -> dict:
    """One row per finding the applicant must act on: what is wrong (and
    why it matters), what we found in their DPR, and what to do -- plain
    sentences, not the raw quote and terse target the table used to show
    (hard to follow, user feedback on PSR/2026/000010)."""
    ordered = sorted(
        _actionable(snapshot), key=lambda f: PRIORITY_ORDER.get(f.priority.value, 9)
    )
    rows = []
    for i, f in enumerate(ordered, start=1):
        custom = CUSTOMER_TEXT.get(f.title) if _is_calculated(f) else None
        if custom:
            title, found, todo = custom
        else:
            title, found, todo = _plain_title(f), _found(f), _customer_action(f)
        found, todo = _explain_terms([found, todo])
        rows.append(
            {
                "n": i,
                "title": title,
                "why": f.why_it_matters or WHY_BY_CATEGORY.get(f.category, DEFAULT_WHY),
                "pages": ", ".join(str(p) for p in f.source_pages),
                "found": found,
                "todo": todo,
                "priority": PRIORITY_LABEL.get(f.priority.value, f.priority.value),
                "chip": PRIORITY_CHIP.get(f.priority.value, "imp"),
            }
        )
    return {"rows": rows}


# Section 3 in the applicant's words (user, 2026-11-25): the snapshot keeps
# the bank's labels (stored report data, also used by the bank-facing CAM);
# only what the page prints is translated. A label not listed prints as is.
# measure -> (plain label, one-line meaning or "").
MEASURE_PLAIN: dict[str, tuple[str, str]] = {
    "Total project cost (₹ lakh)": (
        "Total project cost (₹ lakh)",
        "Everything you need to buy and set up",
    ),
    "Term loan (₹ lakh)": ("Bank loan you are asking for (₹ lakh)", ""),
    "Your contribution (%)": (
        "Your own money (% of the cost)",
        "The share you pay yourself",
    ),
    "Subsidy (₹ lakh)": ("Government subsidy (₹ lakh)", ""),
    "Average DSCR": (
        "Ability to repay, average year (DSCR)",
        "How many times your yearly earnings cover the loan instalments; "
        "1.25 means ₹1.25 earned for every ₹1 due",
    ),
    "Minimum DSCR": (
        "Ability to repay, weakest year (DSCR)",
        "The same figure in your hardest year",
    ),
    "Project IRR (%)": (
        "Yearly return on the project (IRR, %)",
        "What the project earns each year on the money put into it",
    ),
    "Cash break-even (% capacity)": (
        "Break-even point (% of full capacity)",
        "How much of the machine's work you need just to cover your costs",
    ),
    "Debt : equity": (
        "Loan compared with your own money",
        "Rupees of loan for every ₹1 you put in",
    ),
    "Fixed asset coverage": (
        "Asset cover for the loan",
        "Value of your machine and other assets for every ₹1 of loan still owed",
    ),
    "Payback period (years)": ("Time to earn back the cost (years)", ""),
}
ASSUMPTION_PLAIN = {
    "Selling price": "Your price or rate",
    "Capacity utilisation, Yr 1–3": "How much of full capacity you use, years 1–3",
    "Yield / output per unit": "Output per day or per unit",
    "Input cost escalation": "Yearly rise in your costs",
    "Interest rate": "Loan interest rate",
    "Depreciation method / rate": "Yearly wear and tear (depreciation)",
}
# Whole phrases first, then the symbol and abbreviation rules below.
_PLAIN_PHRASES: tuple[tuple[re.Pattern, str], ...] = tuple(
    (re.compile(pattern), plain)
    for pattern, plain in (
        (
            r"^Within unit-cost norms$",
            "In line with usual costs for this kind of project",
        ),
        (r"^As per scheme$", "As the scheme allows"),
        (r"\(scheme ceiling\)", "(scheme limit)"),
        (r"^≤ scheme ceiling$", "Within the scheme's limit"),
        (r"^≥ scheme minimum$", "At least the scheme's minimum"),
        (r"\(your loan rate\)", "(your loan interest rate)"),
        (r"^> cost of capital$", "More than the cost of borrowing"),
        (r"^≤ (\d+(?:\.\d+)?)–(\d+(?:\.\d+)?)%$", r"\1–\2% or less"),
        (r"(\d+(?:\.\d+)?):1$", r"\1 to 1"),
        (r"^< loan tenure \((\S+) yrs\)$", r"Shorter than the loan period (\1 years)"),
        (r"^< loan tenure$", "Shorter than the loan period"),
        (
            r"^Typical ramp-up (\S+) · (\S+) · (\S+)$",
            r"Usually \1 in year 1, \2 in year 2, \3 in year 3",
        ),
        (
            r"^RBI inflation target (\S+)% p\.a\.$",
            r"Prices in India rise about \1% a year (RBI target)",
        ),
        (
            r"^Income Tax: plant (\S+)% WDV, building (\S+)% WDV; "
            r"Companies Act: plant about \S+% SLM$",
            r"Tax rules: machines \1% a year, buildings \2% a year, on the value "
            r"left after earlier years",
        ),
        (
            r"^Use (\S+)% WDV \(or SLM over the useful life\)$",
            r"Use \1% a year on the value left (or spread it evenly over the "
            r"machine's life)",
        ),
        (
            r"^No benchmark for this sector yet$",
            "We have no standard figure for this type of project yet",
        ),
        (r"^No change$", "Fine as it is"),
        (r"^State year-wise utilisation$", "Show the % for each year"),
        (r"^State the rate and its basis$", "Write the rate and where it comes from"),
        (r"^State the method and rate$", "Write the method and the rate"),
        (r"^State it in the assumptions$", "Write it in your assumptions"),
        (r"^State it; ", "Write it down; "),
        (
            r"^Support it with buyer or mandi rates$",
            "Back it with buyer or mandi rates",
        ),
        (r"^≥ ", "At least "),
        (r"^≤ ", "Not more than "),
        (r"^> ", "More than "),
        (r"^< ", "Less than "),
        (r"\bp\.a\.", "a year"),
        (r"\byrs\b", "years"),
    )
)


def _plain_text(text: str | None) -> str:
    if not text:
        return "—"
    for pattern, plain in _PLAIN_PHRASES:
        text = pattern.sub(plain, text)
    return text


# Stress test: a weakest year at or above the bank's floor still repays.
STRESS_FLOOR = Decimal(f"{STRESS_DSCR_FLOOR:.2f}")


def _stress_text(stress) -> str:
    if stress is None:
        return (
            "We could not test this: your DPR does not give the yearly figures needed."
        )
    figures = []
    if stress.average_dscr is not None:
        figures.append(f"{stress.average_dscr:g} in an average year")
    if stress.minimum_dscr is not None:
        figures.append(
            f"{stress.minimum_dscr:g} in your weakest year"
            + (f" ({stress.minimum_year})" if stress.minimum_year else "")
        )
    lowest = (
        stress.minimum_dscr if stress.minimum_dscr is not None else stress.average_dscr
    )
    verdict = (
        "so you would still be able to repay."
        if lowest is not None and lowest >= STRESS_FLOOR
        else "so at that price you would struggle to pay your instalments."
    )
    text = (
        f"If your selling price falls {stress.price_fall_pct}%, your ability to "
        f"repay (DSCR) drops to {' and '.join(figures)}. Banks want at least "
        f"{STRESS_FLOOR}, {verdict}"
    )
    if stress.most_sensitive:
        text += f" {stress.most_sensitive}"
    return text


def build_financials(snapshot: CAMSnapshot) -> dict:
    measures = []
    for m in snapshot.financial_review:
        label, meaning = MEASURE_PLAIN.get(m.measure, (m.measure, ""))
        measures.append(
            {
                "measure": label,
                "meaning": meaning,
                "dpr": m.dpr_value or "—",
                "recomputed": m.recomputed or "—",
                "baseline": _plain_text(m.baseline),
                "status": MEASURE_STATUS[m.status],
                "chip": m.status,
            }
        )
    return {
        "measures": measures,
        "assumptions": [
            {
                "assumption": ASSUMPTION_PLAIN.get(a.assumption, a.assumption),
                "dpr": _plain_text(a.dpr_value),
                "reference": _plain_text(a.reference),
                "suggested": _plain_text(a.suggested),
                "flagged": a.flagged,
            }
            for a in snapshot.assumption_review
        ],
        "stress": _stress_text(snapshot.stress_test),
    }


def build_verification(snapshot: CAMSnapshot) -> dict:
    from .cam_render import _document_verification

    notice = _document_verification(snapshot)
    kyc = snapshot.kyc_verification
    pending = (
        f"Pending: Get your Credit and KYC details verified by {notice['provider']} "
        f"through our {notice['service_name']} Service."
    )
    if kyc is None:
        cards = None
        subtitle = (
            f"To be checked through {notice['provider']}, our regulated partner, "
            "with your consent."
        )
    else:
        subtitle = (
            f"Checked through {kyc.provider} with your consent"
            + (f" on {_date(kyc.consent_date)}" if kyc.consent_date else "")
            + "."
        )

        def status(value):
            return KYC_STATUS.get(value, "Pending") if value else "Pending"

        cards = {
            "score": str(kyc.credit_score) if kyc.credit_score is not None else "—",
            "band": (kyc.credit_band or "—").title(),
            "overdues": f"{kyc.overdues or '—'} · {kyc.write_offs or '—'}",
            "kyc": [
                ("PAN", status(kyc.pan)),
                ("Aadhaar", status(kyc.aadhaar)),
                ("Address", status(kyc.address)),
            ],
            "matches": [
                ("Name: DPR ↔ KYC", status(kyc.name_dpr_kyc)),
                ("Name: KYC ↔ land records", status(kyc.name_kyc_land)),
                ("Location: DPR ↔ land", status(kyc.location_dpr_land)),
            ],
            "before_you_apply": "; ".join(kyc.before_you_apply) or None,
        }
    quotation = (
        f"We validate the vendor quotation under our "
        f"{notice['quotation_service_name']} service"
        + (f" ({notice['quotation_fee']})" if notice["quotation_fee"] else "")
        + "."
        if notice["quotation_service_name"]
        else None
    )
    return {
        "subtitle": subtitle,
        "cards": cards,
        "pending": pending,
        "documents_note": (
            f"This review analyses your DPR as submitted; it does not check that "
            f"your documents are genuine ({'; '.join(notice['documents'])}). "
            f"{notice['provider']} collects and verifies them."
        ),
        "quotation": quotation,
        "documents": [
            {
                "document": d.document,
                "status": DOCUMENT_STATUS[d.status],
                "chip": d.status,
                "action": d.action or "—",
            }
            for d in snapshot.document_checklist
        ],
        "re_review_by": _date(snapshot.re_review_by),
    }


def render_html(snapshot: CAMSnapshot, result=None) -> str:
    from .cam_render import _env

    template = _env().get_template("cam_v2/report.html.jinja")
    return template.render(
        title=f"DPR Pre-submission Review - {reference(snapshot)}",
        accent=ACCENT,
        product=PRODUCT_NAME,
        reference=reference(snapshot),
        prepared_for=snapshot.borrower_profile.borrower_name or "Applicant",
        cover=build_cover(snapshot),
        scheme=build_scheme_fit(snapshot),
        plan=build_action_plan(snapshot),
        fin=build_financials(snapshot),
        ver=build_verification(snapshot),
    )
