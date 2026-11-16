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

from datetime import datetime
from decimal import Decimal

from .cam_snapshot import CAMSnapshot, Money, ReadinessStatus

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
MEASURE_STATUS = {"meets": "Meets", "below": "Below baseline", "pending": "Pending"}
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


def build_action_plan(snapshot: CAMSnapshot) -> dict:
    ordered = sorted(
        _actionable(snapshot), key=lambda f: PRIORITY_ORDER.get(f.priority.value, 9)
    )
    rows = []
    for i, f in enumerate(ordered, start=1):
        rows.append(
            {
                "n": i,
                "title": f.title,
                "why": f.why_it_matters or WHY_BY_CATEGORY.get(f.category, DEFAULT_WHY),
                "pages": ", ".join(str(p) for p in f.source_pages) or "—",
                "dpr": f.dpr_reported_value or "—",
                "should_be": f.should_be or f.recomputed_value or "—",
                "priority": PRIORITY_LABEL.get(f.priority.value, f.priority.value),
                "chip": PRIORITY_CHIP.get(f.priority.value, "imp"),
            }
        )
    return {"rows": rows}


def build_financials(snapshot: CAMSnapshot) -> dict:
    measures = [
        {
            "measure": m.measure,
            "dpr": m.dpr_value or "—",
            "recomputed": m.recomputed or "—",
            "baseline": m.baseline,
            "status": MEASURE_STATUS[m.status],
            "chip": m.status,
        }
        for m in snapshot.financial_review
    ]
    stress = snapshot.stress_test
    if stress is None:
        stress_text = (
            "Pending: the DPR does not give the yearly figures a stress test needs."
        )
    else:
        parts = []
        if stress.average_dscr is not None:
            parts.append(f"average DSCR becomes {stress.average_dscr:g}")
        if stress.minimum_dscr is not None:
            parts.append(
                f"the lowest yearly DSCR becomes {stress.minimum_dscr:g}"
                + (f" ({stress.minimum_year})" if stress.minimum_year else "")
            )
        stress_text = (
            f"If your selling price falls {stress.price_fall_pct}%, "
            + " and ".join(parts)
            + " (the bank expects at least 1.10)."
            + (
                f" Most sensitive assumption: {stress.most_sensitive}."
                if stress.most_sensitive
                else ""
            )
        )
    return {
        "measures": measures,
        "assumptions": [
            {
                "assumption": a.assumption,
                "dpr": a.dpr_value or "—",
                "reference": a.reference or "—",
                "suggested": a.suggested or "—",
                "flagged": a.flagged,
            }
            for a in snapshot.assumption_review
        ],
        "stress": stress_text,
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
