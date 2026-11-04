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
    """The "what we checked" status boxes. Never claims a check happened
    that didn't: a category with zero findings and nothing computed shows
    "Not yet checked", not a false "all clear"."""

    def status_for(categories: set[str], computed: bool) -> tuple[str, str, str]:
        relevant = [f for f in snapshot.findings if f.category in categories]
        if any(f.priority.value == "must_fix" for f in relevant):
            return "Needs attention", _RED, _RED_TINT
        if not computed:
            return "Not yet checked", _GREY, _GREY_TINT
        if relevant:
            return "Issues found", "var(--accent)", "var(--accent-tint)"
        return "No issues found", "var(--brand)", "var(--brand-tint)"

    repayment_status, repayment_color, repayment_tint = status_for(
        {"dscr"}, bool(snapshot.financial_ratios.average_dscr)
    )
    cost_status, cost_color, cost_tint = status_for(
        {"financial_calculation"}, bool(snapshot.project_cost.reconciliation)
    )
    funding_status, funding_color, funding_tint = status_for(
        {"funding"}, bool(snapshot.means_of_finance.funding_gap)
    )
    return [
        {
            "title": "Repayment capacity",
            "status": repayment_status,
            "color": repayment_color,
            "tint": repayment_tint,
        },
        {
            "title": "Project cost",
            "status": cost_status,
            "color": cost_color,
            "tint": cost_tint,
        },
        {
            "title": "Funding plan",
            "status": funding_status,
            "color": funding_color,
            "tint": funding_tint,
        },
        {
            "title": "Identity and KYC",
            "status": "Not yet checked",
            "color": _GREY,
            "tint": _GREY_TINT,
        },
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
    year1 = snapshot.repayment_schedule[0] if snapshot.repayment_schedule else None
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
        "report_reference": snapshot.report_id,
        "report_version": snapshot.report_version,
        "report_date": snapshot.generated_at.strftime("%d %b %Y"),
        "scheme_claimed": (
            snapshot.readiness_assessment.subsidy_benefits[0].scheme_name
            if snapshot.readiness_assessment.subsidy_benefits
            else "None identified"
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
        "report_reference": snapshot.report_id,
        "borrower_name": snapshot.borrower_profile.borrower_name,
        "intro_line": intro_line,
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
    funding_split = (
        {
            "loan_pct": round(loan.amount / total.amount * 100),
            "promoter_pct": round(promoter.amount / total.amount * 100),
            "loan_amount": format_money(loan),
            "promoter_amount": format_money(promoter),
        }
        if loan and promoter and total and total.amount
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

    return {
        "report_reference": snapshot.report_id,
        "borrower_name": snapshot.borrower_profile.borrower_name,
        "tiles": tiles,
        "cost_rows": cost_rows,
        "cost_total": format_money(cost.dpr_reported_total),
        "cost_check": cost_check,
        "funding_split": funding_split,
        "subsidy_label": (
            snapshot.readiness_assessment.subsidy_benefits[0].scheme_name
            if snapshot.readiness_assessment.subsidy_benefits
            else "None identified"
        ),
        "corrections": corrections,
    }


def build_page4_context(snapshot: CAMSnapshot) -> dict:
    """All display values for page4_repayment.html.jinja. The original
    design's bar chart plots five specific acreage points (300/600/910/
    1200-corrected/1200-as-written); three of those (the realistic
    harvest-window range and the DSCR=1.0 break-even acreage) come from
    sector-feasibility/debt-service-threshold calculations this pipeline
    does not yet wire into the engine (PROGRESS.md milestone 4 gap), so
    they are not shown -- only the two real scenarios actually computed
    (`scenario_analysis`) are rendered, as a simple comparison, not a
    fabricated five-point chart."""
    ratios = snapshot.financial_ratios
    year1 = snapshot.repayment_schedule[0] if snapshot.repayment_schedule else None

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
    if ratios.average_dscr and ratios.average_dscr.computed_value is not None:
        dscr_cards.append(
            {
                "label": "Standard bank formula (Year 1)",
                "value": f"{ratios.average_dscr.computed_value}×",
                "note": (
                    "Adds interest back into earnings before comparing to repayment."
                ),
            }
        )

    scenarios = [
        {
            "label": s.label,
            "is_hypothetical": s.is_hypothetical,
            "dscr": (
                f"{s.dscr_by_year[1].computed_value}×"
                if 1 in s.dscr_by_year and s.dscr_by_year[1].computed_value is not None
                else None
            ),
        }
        for s in snapshot.scenario_analysis
    ]

    if ratios.average_dscr and ratios.average_dscr.computed_value is not None:
        lede = (
            f"On the figures in this DPR, Year-1 earnings cover repayment "
            f"{ratios.average_dscr.computed_value}× (standard formula). "
            f"See below for why your DPR's own figure differs."
        )
    else:
        lede = "Repayment coverage could not be computed from this DPR's figures."

    return {
        "report_reference": snapshot.report_id,
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


def render_cam_report_html(
    snapshot: CAMSnapshot,
    *,
    brand_color: str = DEFAULT_BRAND_COLOR,
    accent_color: str = DEFAULT_ACCENT_COLOR,
) -> str:
    env = _env()
    template = env.get_template("cam/report.html.jinja")
    return template.render(
        title=f"Loan Readiness Report - {snapshot.report_id}",
        brand=brand_color,
        accent=accent_color,
        ctx=build_page1_context(snapshot),
        ctx2=build_page2_context(snapshot),
        ctx3=build_page3_context(snapshot),
        ctx4=build_page4_context(snapshot),
    )


def render_cam_report_pdf(
    snapshot: CAMSnapshot,
    *,
    brand_color: str = DEFAULT_BRAND_COLOR,
    accent_color: str = DEFAULT_ACCENT_COLOR,
) -> bytes:
    """Renders the CAM report to PDF bytes. No network access is made:
    fonts and the stylesheet are local files resolved via `base_url`."""
    from weasyprint import HTML

    html = render_cam_report_html(
        snapshot, brand_color=brand_color, accent_color=accent_color
    )
    return HTML(string=html, base_url=str(CAM_TEMPLATES_DIR)).write_pdf()
