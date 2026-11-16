"""Renders a validated CAMSnapshot as the six-page CAM report PDF (brief
Sections 2.1/14). WeasyPrint (pure Python, no browser binary, strong CSS
Paged Media support) is the chosen PDF engine -- do not add a second one.

Template source of truth: `app/templates/cam/*.html.jinja`, ported from
`cam-design-reference/*-html.zip` (see PROGRESS.md milestone 4). Jinja2
autoescape is on (default for `.html`/`.jinja` via `select_autoescape`), so
every CAMSnapshot string value is escaped automatically; nothing in this
module or the templates uses `|safe` on model data, which is how an
LLM-authored finding title/explanation could otherwise inject markup.

All display-value computation (readiness copy, money formatting, which
"what we checked" box shows which status) lives in this module as plain,
unit-testable Python -- the templates only loop and substitute, no
business logic in Jinja.

Pages 1-2 (Summary, Fix before you apply) are wired up so far; see
PROGRESS.md for the remaining four.
"""

from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .cam_snapshot import CAMSnapshot, Money, ReadinessStatus

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"
CAM_TEMPLATES_DIR = TEMPLATES_DIR / "cam"

DEFAULT_BRAND_COLOR = "#1F5E46"
DEFAULT_ACCENT_COLOR = "#9A5408"

_RED = "#9B1C1C"
_RED_TINT = "#9B1C1C10"
_GREY = "#5B6560"
_GREY_TINT = "#F1F3F2"


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "jinja"]),
    )


def _public_report_reference(snapshot: CAMSnapshot) -> str:
    """Stable display ID without exposing internal Wallet person/mobile references."""
    digest = sha256(snapshot.report_id.encode("utf-8")).hexdigest()[:10].upper()
    return f"LAS-CAM-{snapshot.generated_at.year}-{digest}"


def format_money(money: Money | None) -> str | None:
    if money is None:
        return None
    unit_label = {"absolute": "", "lakh": " lakh", "crore": " crore"}[money.unit]
    return f"₹ {money.amount:,.2f}{unit_label}"


def format_pct(part: Decimal, whole: Decimal) -> str | None:
    if whole == 0:
        return None
    return f"{(part / whole * 100):.0f}%"


def _readiness_copy(snapshot: CAMSnapshot) -> dict:
    """Readiness status display copy. Deliberately describes DPR
    readiness only -- never a loan-approval recommendation (Section
    19.20): the original design's "Recommend"/"Not recommended" outcome
    language is not reused."""
    status = snapshot.readiness_assessment.status
    must_fix_count = sum(1 for f in snapshot.findings if f.priority.value == "must_fix")
    copy_by_status = {
        ReadinessStatus.READY_FOR_SUBMISSION_REVIEW: {
            "color": "var(--brand)",
            "tint": "var(--brand-tint)",
            "badge": "Ready for submission review",
            "headline": "No must-fix issues were found",
            "body": (
                "This DPR has no unresolved must-fix issues. A consultant "
                "or your Business Associate should still confirm it before "
                "you submit it to the bank."
            ),
        },
        ReadinessStatus.CORRECTIONS_REQUIRED: {
            "color": "var(--accent)",
            "tint": "var(--accent-tint)",
            "badge": f"{must_fix_count} issue(s) to fix before you apply",
            "headline": "Fix a few issues before you apply",
            "body": (
                "This DPR has issues that should be corrected first. Page 2 "
                "lists what to fix, then this report can be re-run."
            ),
        },
        ReadinessStatus.EXPERT_REVIEW_REQUIRED: {
            "color": _RED,
            "tint": _RED_TINT,
            "badge": "Needs expert review",
            "headline": "This DPR needs a closer look",
            "body": (
                "Critical issues were found in this DPR. These need expert "
                "review before this proposal is ready to go to a lender."
            ),
        },
        ReadinessStatus.INSUFFICIENT_INFORMATION: {
            "color": _GREY,
            "tint": _GREY_TINT,
            "badge": "Insufficient information",
            "headline": "This analysis could not be completed",
            "body": (
                "Not enough information was available to assess this DPR. "
                "See the outstanding items in this report."
            ),
        },
    }[status]

    score_mode = snapshot.readiness_assessment.score_mode
    score = (
        snapshot.readiness_assessment.total_score
        if score_mode == "dimensions"
        else None
    )
    dimensions = (
        [
            {"label": d.dimension.replace("_", " ").title(), "score": d.score}
            for d in snapshot.readiness_assessment.dimension_scores
        ]
        if score_mode == "dimensions"
        else []
    )
    # Circle circumference for r=50 is ~314; dasharray is the filled arc
    # length, never computed from an LLM-authored number.
    score_arc = round((score or 0) / 100 * 314)

    return {
        **copy_by_status,
        "score_mode": score_mode,
        "score": score,
        "score_arc": score_arc,
        "dimensions": dimensions,
    }


def _checks(snapshot: CAMSnapshot) -> list[dict]:
    """Describe checks performed; don't equate missing computation with no review.

    Detection of a discrepancy counts as a review, not as verification of
    the financial figure or of KYC identity.
    """
    def outcome(categories: set[str], *, assessed: bool) -> tuple[str, str, str]:
        relevant = [f for f in snapshot.findings if f.category.casefold() in categories]
        if relevant:
            urgent = any(f.priority.value == "must_fix" for f in relevant)
            return (("Needs attention" if urgent else "Issues found"),
                    _RED if urgent else "var(--accent)",
                    _RED_TINT if urgent else "var(--accent-tint)")
        if assessed:
            return "No issues found", "var(--brand)", "var(--brand-tint)"
        return "Not yet checked", _GREY, _GREY_TINT

    repayment_categories = {"dscr", "repayment", "debt_service"}
    cost_categories = {"project_cost", "cost", "financial_calculation", "inconsistency", "arithmetic"}
    funding_categories = {"funding", "means_of_finance"}
    repayment = outcome(repayment_categories,
        assessed=bool(snapshot.repayment_schedule))
    cost = outcome(cost_categories,
        assessed=bool(snapshot.project_cost.components or snapshot.project_cost.dpr_reported_total))
    funding = outcome(funding_categories,
        assessed=bool(snapshot.means_of_finance.components or snapshot.means_of_finance.requested_loan))
    identity_findings = [f for f in snapshot.findings if (
        "identity" in f.category.casefold() or "borrower" in f.title.casefold()
        or "applicant name" in f.title.casefold() or "name mismatch" in f.title.casefold()
    )]
    identity = (("Identity conflict — verify KYC", _RED, _RED_TINT)
                if identity_findings else ("Not yet checked", _GREY, _GREY_TINT))
    return [
        {"title": title, "status": value[0], "color": value[1], "tint": value[2]}
        for title, value in (
            ("Repayment capacity", repayment), ("Project cost", cost),
            ("Funding plan", funding), ("Identity and KYC", identity)
        )
    ]


def _finding_counts(snapshot: CAMSnapshot) -> dict:
    return {
        "verified_calculation_errors": sum(
            1 for f in snapshot.findings if f.value_classification.value == "recomputed"
        ),
        "feasibility_risks": sum(
            1 for f in snapshot.findings if f.finding_class.value == "business_risk"
        ),
        "missing_evidence": sum(
            1 for f in snapshot.findings if not f.evidence and not f.source_pages
        ),
        "outstanding_clarifications": (
            snapshot.readiness_assessment.outstanding_clarifications_count
        ),
    }


def build_page1_context(snapshot: CAMSnapshot) -> dict:
    """All display values for page1_summary.html.jinja, computed from the
    snapshot alone -- no hardcoded sample data (Section 19.3/19.4)."""
    cost = snapshot.project_cost.dpr_reported_total
    loan = snapshot.means_of_finance.requested_loan
    promoter = snapshot.means_of_finance.promoter_contribution
    year1 = next((r for r in snapshot.repayment_schedule if r.year == 1), None)
    verified_benefit = next(
        (
            b
            for b in snapshot.readiness_assessment.subsidy_benefits
            if b.eligibility_status != "unverified"
        ),
        None,
    )

    project_summary_line = (
        f"Analysis of the Detailed Project Report for "
        f"{snapshot.project_profile.project_name}"
        + (
            f" in {snapshot.project_profile.project_location}."
            if snapshot.project_profile.project_location
            else "."
        )
        if snapshot.project_profile.project_name
        else "Project details are not yet available for this analysis."
    )

    return {
        "report_reference": _public_report_reference(snapshot),
        "report_version": snapshot.report_version,
        "report_date": snapshot.generated_at.strftime("%d %b %Y"),
        "scheme_claimed": (
            snapshot.project_profile.scheme_claimed
            or (snapshot.readiness_assessment.subsidy_benefits[0].scheme_name
                if snapshot.readiness_assessment.subsidy_benefits else "Not verified")
        ),
        "bd_name": snapshot.borrower_profile.bd_name or "Not recorded",
        "service_request_id": snapshot.service_request_id,
        "borrower_name": snapshot.borrower_profile.borrower_name,
        "project_summary_line": project_summary_line,
        "readiness": _readiness_copy(snapshot),
        "loan": {
            "requested_amount": format_money(loan),
            "requested_sub": (
                f"{format_pct(loan.amount, cost.amount)} of a "
                f"{format_money(cost)} project"
                if loan and cost
                else None
            ),
            "year1_debt_service": (
                format_money(year1.total_debt_service) if year1 else None
            ),
            "year1_debt_service_sub": (
                f"{format_money(year1.principal_due)} principal + "
                f"{format_money(year1.interest_due)} interest"
                if year1
                else None
            ),
            "promoter_amount": format_money(promoter),
            "promoter_sub": (
                f"{format_pct(promoter.amount, cost.amount)} of the project cost"
                if promoter and cost
                else None
            ),
        },
        "subsidy": (
            {
                "label": f"{verified_benefit.scheme_name} benefit",
                "amount": format_money(verified_benefit.eligible_amount),
                "sub": verified_benefit.effective_period or "",
            }
            if verified_benefit
            else {
                "label": "Capital subsidy",
                "amount": None,
                "sub": (
                    "No applicable capital subsidy has been verified from "
                    "the available sources."
                ),
            }
        ),
        "checks": _checks(snapshot),
        "finding_counts": _finding_counts(snapshot),
    }


def _finding_ref(finding) -> str:
    """Page-reference label shown beside a finding. Prefers the finding's
    own source pages; falls back to its category when no page is known --
    never fabricates a page number (Section 19.3)."""
    if finding.source_pages:
        return ", ".join(f"p.{p}" for p in finding.source_pages)
    return finding.category.replace("_", " ").title()


def build_page2_context(snapshot: CAMSnapshot) -> dict:
    """All display values for page2_fix_before_you_apply.html.jinja, built
    entirely from CAMSnapshot.findings -- priority/severity grouping is
    computed upstream by finding_rules.py, never decided in the template."""
    must_fix = [f for f in snapshot.findings if f.priority.value == "must_fix"]
    should_fix = [f for f in snapshot.findings if f.priority.value == "should_fix"]
    good_to_fix = [f for f in snapshot.findings if f.priority.value == "good_to_fix"]

    def _entry(f) -> dict:
        return {
            "title": f.title,
            "explanation": f.explanation,
            "suggested_action": f.suggested_action,
            "ref": _finding_ref(f),
        }

    total = len(must_fix) + len(should_fix) + len(good_to_fix)
    if total == 0:
        intro_line = "No issues were found in this DPR."
    elif must_fix:
        intro_line = (
            f"We found {total} issue(s) in your DPR. Fix the "
            f"{len(must_fix)} “must fix” item(s) before your BD "
            f"submits to the bank."
        )
    else:
        intro_line = (
            f"We found {total} issue(s) in your DPR. None are must-fix, "
            f"but review them before your BD submits to the bank."
        )

    return {
        "report_reference": _public_report_reference(snapshot),
        "borrower_name": snapshot.borrower_profile.borrower_name,
        "intro_line": intro_line,
        "must_fix_total": len(must_fix),
        "should_fix_total": len(should_fix),
        "good_to_fix_total": len(good_to_fix),
        "has_more": len(must_fix)>3 or len(should_fix)>2 or len(good_to_fix)>0,
        "must_fix": [_entry(f) for f in must_fix],
        "should_fix": [_entry(f) for f in should_fix],
        "good_to_fix": [_entry(f) for f in good_to_fix],
    }


def build_page3_context(snapshot: CAMSnapshot) -> dict:
    """All display values for page3_project_cost.html.jinja. Project cost
    and means-of-finance breakdowns are only as populated as the pipeline
    has extracted (Section 19.3/19.4 -- a missing breakdown shows an
    explicit empty state, never a fabricated line item)."""
    profile = snapshot.project_profile
    tiles = [
        {"label": "Activity", "value": profile.project_name},
        {"label": "Location", "value": profile.project_location},
        {
            "label": "Sector",
            "value": (
                f"{profile.business_sector} — {profile.business_subsector}"
                if profile.business_sector and profile.business_subsector
                else profile.business_sector or profile.business_subsector
            ),
        },
        {"label": "Purpose", "value": profile.project_purpose},
    ]

    cost = snapshot.project_cost
    cost_rows = [
        {
            "label": line.label,
            "amount": format_money(line.dpr_reported) or "Requires verification",
        }
        for line in cost.components
    ]
    reconciliation = cost.reconciliation
    if reconciliation is None:
        cost_check = None
    elif reconciliation.status == "COMPUTED" and (
        reconciliation.computed_value is None or reconciliation.computed_value == 0
    ):
        cost_check = {"label": "Matches funding", "kind": "ok"}
    else:
        cost_check = {"label": "Requires verification", "kind": "warn"}

    finance = snapshot.means_of_finance
    total = finance.dpr_reported_total
    loan = finance.requested_loan
    promoter = finance.promoter_contribution
    # Display an explicitly known loan+promoter split even when the DPR has
    # no separately extracted funding-total cell. The computed denominator
    # is a display ratio, NOT represented as the DPR's reported total.
    funding_base = (loan.amount + promoter.amount) if loan and promoter else None
    funding_split = (
        {
            "loan_pct": round(loan.amount / funding_base * 100),
            "promoter_pct": round(promoter.amount / funding_base * 100),
            "loan_amount": format_money(loan),
            "promoter_amount": format_money(promoter),
        }
        if loan and promoter and funding_base and funding_base > 0
        else None
    )

    corrections = [
        {
            "title": f.title,
            "dpr_value": f.dpr_reported_value,
            "should_be": f.recomputed_value,
            "why": f.explanation,
        }
        for f in snapshot.findings
        if f.dpr_reported_value and f.recomputed_value
    ]

    action_corrections = [
        {"title": f.title, "action": f.suggested_action,
         "ref": ", ".join("p." + str(page) for page in f.source_pages)}
        for f in snapshot.findings
        if f.suggested_action and not (f.dpr_reported_value and f.recomputed_value)
        and f.priority.value in ("must_fix", "should_fix")
    ][:1]
    # AIF is ordinarily discussed as interest subvention/guarantee, not
    # capital subsidy. A scheme *claim* must never assert capital support.
    scheme_claim = snapshot.project_profile.scheme_claimed
    return {
        "report_reference": _public_report_reference(snapshot),
        "borrower_name": snapshot.borrower_profile.borrower_name,
        "tiles": tiles,
        "cost_rows": cost_rows,
        "cost_total": format_money(cost.dpr_reported_total),
        "cost_check": cost_check,
        "funding_split": funding_split,
        "subsidy_label": "Not independently verified",
        "claimed_scheme": scheme_claim,
        "corrections": corrections,
        "action_corrections": action_corrections if not cost_rows else [],
    }


def build_page4_context(snapshot: CAMSnapshot) -> dict:
    """All display values for page4_repayment.html.jinja. The original
    design's bar chart plots five specific acreage points; two of those
    (a "realistic harvest-window range" and a specific DSCR=1.0 break-even
    acreage) were a judgment call baked into the design reference, not a
    reproducible pipeline output. This pipeline's own debt-service-
    threshold calculation (wired in milestone 4b) is shown instead, as
    whatever value it actually computes -- which may differ from the
    design reference's own figure, since that figure's derivation is not
    reproducible from this engine's formulas. The two real scenarios
    actually computed (`scenario_analysis`) are rendered as a simple
    comparison, not a fabricated five-point chart."""
    ratios = snapshot.financial_ratios
    year1 = next((r for r in snapshot.repayment_schedule if r.year == 1), None)

    dscr_cards = []
    if ratios.dscr_pat_dep_only and ratios.dscr_pat_dep_only.computed_value is not None:
        dscr_cards.append(
            {
                "label": "Your DPR states (Year 1)",
                "value": f"{ratios.dscr_pat_dep_only.computed_value}×",
                "note": (
                    "Profit + depreciation ÷ repayment. Leaves interest "
                    "out of earnings."
                ),
            }
        )
    # An average across years is NOT a valid Year-1 ratio fallback.
    year1_dscr = year1.dscr if year1 and year1.dscr else None
    if year1_dscr and year1_dscr.computed_value is not None:
        dscr_cards.append(
            {
                "label": "Standard bank formula (Year 1)",
                "value": f"{year1_dscr.computed_value}×",
                "note": (
                    "Adds interest back into earnings before comparing to repayment."
                ),
            }
        )

    # Page 4's own labels for the two cost scenarios -- distinct from
    # ScenarioAnalysis.label (used elsewhere, e.g. the narrative "As
    # stated in the DPR at 1200.0 acres" description), chosen so a
    # borrower reads "whose costs" rather than "which acreage".
    _scenario_display_label = {
        "dpr_as_written": "Using your DPR's costs",
        "corrected_costs": "If flagged fuel and crew corrections are confirmed",
    }

    def _trace_value(trace, unit: str) -> str | None:
        if trace and trace.status == "COMPUTED" and trace.computed_value is not None:
            return f"{trace.computed_value}{unit}"
        return None

    scenarios = [
        {
            "label": _scenario_display_label.get(s.scenario_id, s.label),
            "is_hypothetical": s.is_hypothetical,
            "dscr": (
                f"{s.dscr_by_year[1].computed_value}×"
                if 1 in s.dscr_by_year and s.dscr_by_year[1].computed_value is not None
                else None
            ),
            "breakeven_accounting": _trace_value(s.breakeven_accounting, " acres/year"),
            "breakeven_operating_cashflow": _trace_value(
                s.breakeven_operating_cashflow, " acres/year"
            ),
            "breakeven_debt_service_threshold": _trace_value(
                s.breakeven_debt_service_threshold, " acres/year"
            ),
        }
        for s in snapshot.scenario_analysis
    ]

    if year1_dscr and year1_dscr.computed_value is not None:
        lede = (
            f"On the figures in this DPR, Year-1 earnings cover repayment "
            f"{year1_dscr.computed_value}× (stated DSCR definition). "
            f"See below for why your DPR's own figure differs."
        )
    else:
        lede = "Repayment coverage could not be computed from this DPR's figures."

    return {
        "report_reference": _public_report_reference(snapshot),
        "borrower_name": snapshot.borrower_profile.borrower_name,
        "lede": lede,
        "dscr_cards": dscr_cards,
        "scenarios": scenarios,
        "year1": (
            {
                "principal": format_money(year1.principal_due),
                "interest": format_money(year1.interest_due),
                "total": format_money(year1.total_debt_service),
            }
            if year1
            else None
        ),
    }


def build_page5_context(snapshot: CAMSnapshot) -> dict:
    """All display values for page5_security.html.jinja. CAMSnapshot has
    no collateral/guarantor/disbursement model -- rows this pipeline
    cannot assess read "Not assessed by this analysis", never "Not stated
    in your DPR" (that would claim we checked the document and found it
    absent, which we did not; Section 19.3). The post-delivery obligations
    checklist is generic equipment-loan advisory content, gated to the
    harvester sector this pipeline actually models -- shown as explicitly
    unavailable for any other sector rather than reused wholesale."""
    profile = snapshot.project_profile
    cost_total = snapshot.project_cost.dpr_reported_total
    machinery = ", ".join(profile.machinery_equipment) or None

    security_rows = [
        {
            "label": "Machine (hypothecation)",
            "details": (
                f"The {machinery} is the bank's security until the loan is repaid"
                if machinery
                else "Equipment financed by this loan, once confirmed"
            ),
            "value": format_money(cost_total) or "Requires verification",
            "status": "Included" if machinery else "To verify",
            "status_kind": "ok" if machinery else "warn",
        },
        {
            "label": "Credit guarantee",
            "details": "A scheme credit guarantee may reduce other security needed",
            "value": "—",
            "status": "To verify",
            "status_kind": "warn",
        },
        {
            "label": "Land or other collateral",
            "details": "Not assessed by this analysis",
            "value": "—",
            "status": "Confirm with lender",
            "status_kind": "warn",
        },
        {
            "label": "Guarantor",
            "details": "Not assessed by this analysis",
            "value": "—",
            "status": "Confirm with lender",
            "status_kind": "warn",
        },
        {
            "label": "Machine insurance",
            "details": (
                f"First year included in the {format_money(cost_total)} "
                f"cost; renew every year, bank as loss payee"
                if cost_total
                else "Renew every year once cost is confirmed; bank as loss payee"
            ),
            "value": "In cost" if cost_total else "Requires verification",
            "status": "Renew yearly",
            "status_kind": "accent",
        },
    ]

    promoter = snapshot.means_of_finance.promoter_contribution
    loan = snapshot.means_of_finance.requested_loan
    disbursement_steps = [
        {
            "title": "You pay your share",
            "detail": (
                f"{format_money(promoter)} to the dealer first"
                if promoter
                else "Your contribution, paid to the dealer first"
            ),
        },
        {
            "title": "The lender pays the dealer",
            "detail": (
                f"{format_money(loan)} against the invoice"
                if loan
                else "The loan amount, against the invoice"
            ),
        },
        {
            "title": "Bank records its charge",
            "detail": "Equipment registered as security",
        },
    ]

    is_harvester = (
        bool(machinery and "harvester" in machinery.lower())
        or "harvester" in (profile.business_subsector or "").lower()
    )
    after_delivery_checklist = (
        [
            {
                "task": (
                    "Send a geo-tagged photo of the equipment with its serial number"
                ),
                "when": "Within 30 days of delivery",
            },
            {"task": "Pay each instalment on time", "when": "As per sanction"},
            {
                "task": "Keep a dealer service agreement for in-season breakdowns",
                "when": "Renew before each season",
            },
            {
                "task": "Renew machine insurance with the bank as loss payee",
                "when": "Every year",
            },
            {
                "task": (
                    "Ask the bank before selling, moving or lending out the equipment"
                ),
                "when": "Any time",
            },
        ]
        if is_harvester
        else []
    )

    return {
        "report_reference": _public_report_reference(snapshot),
        "borrower_name": snapshot.borrower_profile.borrower_name,
        "security_rows": security_rows,
        "disbursement_steps": disbursement_steps,
        "after_delivery_checklist": after_delivery_checklist,
    }


def build_page6_context(snapshot: CAMSnapshot) -> dict:
    """All display values for page6_next_steps.html.jinja. The original
    design's benefits cards (a specific AIF interest-subvention rupee
    figure, a credit-guarantee note) and eligibility rows (AIF
    registration, page-count-vs-26, a specific 0.45x-at-600-acres
    repayment figure) have no corresponding field anywhere in
    CAMSnapshot -- this pipeline computes no subvention amount and
    tracks no "expected page count" or acreage-specific repayment
    scenario (same gaps noted on Pages 3/4). Rather than reuse them, this
    page builds a generic eligibility checklist and action list from what
    the snapshot actually has: subsidy_benefits, means_of_finance,
    findings, and outstanding_clarifications_count. "SBI"/"[Branch
    name]" are replaced by the generic "your lender" -- CAMSnapshot never
    records which bank (Section 19.3)."""
    must_fix_count = sum(1 for f in snapshot.findings if f.priority.value == "must_fix")
    outstanding = snapshot.readiness_assessment.outstanding_clarifications_count
    promoter = snapshot.means_of_finance.promoter_contribution
    verified_benefit = next(
        (
            b
            for b in snapshot.readiness_assessment.subsidy_benefits
            if b.eligibility_status != "unverified"
        ),
        None,
    )

    benefit_card = (
        {
            "label": f"{verified_benefit.scheme_name} benefit",
            "value": format_money(verified_benefit.eligible_amount) or "To verify",
            "note": verified_benefit.effective_period or "",
        }
        if verified_benefit
        else {
            "label": "Capital subsidy",
            "value": "Eligibility not verified",
            "note": (
                "Scheme details and eligibility require authoritative confirmation."
            ),
        }
    )

    checklist = [
        {
            "requirement": "Your contribution",
            "found": (format_money(promoter) if promoter else "Not yet confirmed"),
            "status": "Met" if promoter else "Action needed",
            "met": bool(promoter),
        },
        {
            "requirement": "Must-fix issues from page 2",
            "found": (
                "None outstanding"
                if must_fix_count == 0
                else f"{must_fix_count} unresolved"
            ),
            "status": "Met" if must_fix_count == 0 else "Action needed",
            "met": must_fix_count == 0,
        },
        {
            "requirement": "Outstanding clarifications",
            "found": (
                "None outstanding" if outstanding == 0 else f"{outstanding} unresolved"
            ),
            "status": "Met" if outstanding == 0 else "Action needed",
            "met": outstanding == 0,
        },
    ]

    action_items = []
    if must_fix_count:
        action_items.append(
            {
                "title": f"Revise your DPR to fix the {must_fix_count} must-fix "
                f"item(s) on page 2",
                "detail": "Your consultant can update the projections.",
            }
        )
    if outstanding:
        bd_name = snapshot.borrower_profile.bd_name or "your Business Associate"
        action_items.append(
            {
                "title": "Share the outstanding documents",
                "detail": f"Share them with {bd_name} through the LAS customer screen.",
            }
        )

    # Shown as its own section on page 6 (always), not as an action item.
    verification = _document_verification(snapshot)

    bd_name = snapshot.borrower_profile.bd_name or "your Business Associate"
    next_steps = [
        {"title": "Fix your DPR", "detail": "You and your consultant"},
        {"title": "We re-run this report", "detail": "LAS"},
        {"title": "Application submitted", "detail": f"{bd_name} to your lender"},
        {"title": "Lender review", "detail": "Your lender's branch"},
    ]

    return {
        "report_reference": _public_report_reference(snapshot),
        "borrower_name": snapshot.borrower_profile.borrower_name,
        "benefit_card": benefit_card,
        "checklist": checklist,
        "action_items": action_items,
        "next_steps": next_steps,
        "bd_name": bd_name,
        "verification": verification,
    }


def _document_verification(snapshot: CAMSnapshot) -> dict:
    """The "documents not verified" notice for page 6. Reports made before
    the notice existed show it without a fee (the fee then is unknown)."""
    from .cam_snapshot import DOCUMENTS_NOT_VERIFIED

    notice = snapshot.document_verification
    fee = notice.service_fee_inr if notice else None
    q_fee = notice.quotation_service_fee_inr if notice else None
    return {
        "provider": notice.provider if notice else "IFPL",
        "service_name": notice.service_name if notice else "Value Addition",
        "fee": f"Rs {fee:,.0f}" if fee is not None else None,
        "documents": list(notice.documents if notice else DOCUMENTS_NOT_VERIFIED),
        "quotation_service_name": notice.quotation_service_name if notice else None,
        "quotation_fee": f"Rs {q_fee:,.0f}" if q_fee is not None else None,
    }


def uses_v2_layout(snapshot: CAMSnapshot) -> bool:
    """2.x snapshots print the "DPR Pre-submission Review" layout
    (cam_render_v2.py); 1.x ones keep this six-page layout, so a report
    already issued never changes."""
    return int(snapshot.schema_version.split(".")[0]) >= 2


def render_cam_report_html(
    snapshot: CAMSnapshot,
    *,
    brand_color: str = DEFAULT_BRAND_COLOR,
    accent_color: str = DEFAULT_ACCENT_COLOR,
    result=None,
) -> str:
    if uses_v2_layout(snapshot):
        from .cam_render_v2 import render_html

        return render_html(snapshot, result)
    env = _env()
    template = env.get_template("cam/report.html.jinja")
    return template.render(
        title=f"Loan Readiness Report - {_public_report_reference(snapshot)}",
        brand=brand_color,
        accent=accent_color,
        ctx=build_page1_context(snapshot),
        ctx2=build_page2_context(snapshot),
        ctx3=build_page3_context(snapshot),
        ctx4=build_page4_context(snapshot),
        ctx5=build_page5_context(snapshot),
        ctx6=build_page6_context(snapshot),
    )


def render_cam_report_pdf(
    snapshot: CAMSnapshot,
    *,
    brand_color: str = DEFAULT_BRAND_COLOR,
    accent_color: str = DEFAULT_ACCENT_COLOR,
    result=None,
) -> bytes:
    """Renders the CAM report to PDF bytes. No network access is made:
    fonts and the stylesheet are local files resolved via `base_url`.
    `result` (the AnalysisResult) adds the audit details to the 2.x
    appendix; it is optional."""
    from weasyprint import HTML

    html = render_cam_report_html(
        snapshot, brand_color=brand_color, accent_color=accent_color, result=result
    )
    base = TEMPLATES_DIR if uses_v2_layout(snapshot) else CAM_TEMPLATES_DIR
    return HTML(string=html, base_url=str(base)).write_pdf()
