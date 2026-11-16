"""Deterministic finding classification: dedup key, severity-on-merge,
defect-vs-business-risk, and the Must fix / Should fix / Good to fix
priority table (brief Section 7). No LLM involved anywhere in this module --
an agent may describe a problem, but code alone decides how urgent it is.

Priority rule table (defaults; override via Settings, not by editing this
file per-deployment):

- Must fix: a code-verified cost/means-of-finance/DSCR mismatch at or above
  `must_fix_impact_pct` of the stated total, or a year-1 DSCR below 1.0, or
  category == "missing_document", or severity == "critical".
- Should fix: a code-verified mismatch below that threshold, or severity ==
  "high" (narrative).
- Good to fix: everything else (medium/low/info; completeness gaps).

`priority_for_calculation()` has the real percentage-impact numbers because
financial_calc.CalculationResult carries computed/stated/discrepancy;
`priority_for_narrative()` is a severity-based fallback for findings that
never ran through a CalculationResult (the Finance/Sector/Risk agents' own
prose) -- it does not know the dollar impact, which is a real limitation,
documented in PROGRESS.md milestone 2.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from .schemas import Finding

_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

# Categories a human reviewing the DPR itself would call a *defect* in the
# document: an error, inconsistency or omission. Everything else is treated
# as a property of the underlying project (a business risk) -- see
# PROGRESS.md for why this is a category heuristic rather than an
# agent-emitted tag (Section 4.1: extend an agent schema only when a field
# truly cannot be derived any other way; this one can, approximately, from
# categories the existing prompts already use).
_DOCUMENT_DEFECT_CATEGORIES = {
    "financial_calculation",
    "dscr",
    "interest_subvention",
    "funding",
    "inconsistency",
    "arithmetic",
    "missing_document",
    "identity_mismatch",
    "contradiction",
    # From schemas.FINDING_CATEGORIES (the agents' enforced vocabulary).
    "identity",
    "project_cost",
}


@dataclass(frozen=True)
class PriorityThresholds:
    must_fix_impact_pct: float = 5.0


DEFAULT_THRESHOLDS = PriorityThresholds()


def classify_finding_class(finding: Finding) -> str:
    """Returns "document_defect" or "business_risk"."""
    return (
        "document_defect"
        if finding.category in _DOCUMENT_DEFECT_CATEGORIES
        else "business_risk"
    )


def compute_dedup_key(finding: Finding) -> str:
    """Group the *same* issue even when multiple agents paraphrase its title.

    Tight topic+page rules avoid merging independent discrepancies on nearby
    schedules. Never group a calculated mismatch into unrelated LLM advice.
    """
    text = f"{finding.title} {finding.explanation[:250]}".casefold()
    page = finding.evidence_page if finding.evidence_page is not None else "unknown"
    topics = (
        ("omitted_interest_pnl", ("interest", "profit", "omit")),
        ("missing_vendor_quote", ("quotation", "vendor")),
        ("missing_vendor_quote", ("pro-forma", "invoice")),
        ("working_capital_margin", ("working capital", "margin")),
        ("harvesting_operating_days", ("operating", "days", "harvest")),
        ("day_one_full_utilization", ("100%", "capacity")),
        ("interest_schedule_mismatch", ("interest", "schedule", "mismatch")),
    )
    title = finding.title.casefold()
    # These three common wordings describe the SAME acreage-throughput vs
    # harvest-window mismatch, not three unrelated financial defects. Require
    # all concept groups on the same cited page to avoid overmerging.
    if (page != "unknown"
            and ("acre" in title or "throughput" in title)
            and ("harvest" in title or "season" in title)
            and ("throughput" in title or "operating" in title or "capacity" in title)):
        return f"issue:harvest_window_capacity:{page}"
    for topic, needles in topics:
        # The title must name the topic: matching needles in the
        # explanation alone over-merged (a fuel-cost finding whose prose
        # mentions "harvester operating days" became the operating-days
        # issue) and split identical titles (a calculated acreage finding
        # and its narrative restatement got different keys).
        if all(word in text for word in needles) and any(
            word in title for word in needles
        ):
            return f"issue:{topic}:{page}"
    # Unlike original exact-title matching, punctuation and filler words
    # no longer produce distinct keys; don't merge across different pages.
    words = re.findall(r"[a-z0-9]+", finding.title.casefold())
    stop = {"the", "a", "an", "of", "in", "for", "and", "with", "from", "to", "on", "dpr", "year", "report", "financial"}
    canonical = " ".join(sorted({w for w in words if w not in stop}))
    return f"{finding.category}:{canonical}:{page}"


def pick_consolidated_severity(findings: list[Finding]) -> str:
    """The most severe rating among duplicates wins -- never whichever
    duplicate happened to be produced first (Section 5.1 point 3)."""
    return min(findings, key=lambda f: _SEVERITY_RANK.get(f.severity, 99)).severity


def priority_for_calculation(
    *,
    check: str,
    computed_value: float | None,
    stated_value: float | None,
    thresholds: PriorityThresholds = DEFAULT_THRESHOLDS,
) -> tuple[str, str]:
    """Priority for a finding built from a financial_calc.CalculationResult.
    Returns (priority, explanation_of_which_rule_fired)."""
    year_one_insolvent = (
        check.startswith("dscr_year_1")
        and computed_value is not None
        and computed_value < 1.0
    )
    if year_one_insolvent:
        return (
            "must_fix",
            "MUST_FIX: year-1 DSCR below 1.0 -- the project's own figures "
            "show it cannot service its debt in the first year.",
        )
    if check == "year1_full_utilisation":
        return (
            "must_fix",
            "MUST_FIX: full capacity assumed from year 1 (reference CAM "
            "template, must-fix #4).",
        )
    if check == "negative_contribution":
        return (
            "must_fix",
            "MUST_FIX: the price per unit does not cover the variable cost "
            "per unit.",
        )
    if check == "operating_days_idle":
        return (
            "should_fix",
            "SHOULD_FIX: the asset is idle most of the year (reference CAM "
            "template, should-fix #9).",
        )
    if check == "revenue_rebuild" and stated_value and computed_value is not None:
        impact_pct = abs(computed_value - stated_value) / abs(stated_value) * 100
        material = impact_pct >= thresholds.must_fix_impact_pct
        if stated_value > computed_value and material:
            return (
                "must_fix",
                f"MUST_FIX: stated revenue is {impact_pct:.1f}% above what the "
                "document's own volume and price give, at/above the "
                f"{thresholds.must_fix_impact_pct:.0f}% threshold.",
            )
        return (
            "should_fix",
            f"SHOULD_FIX: stated revenue differs by {impact_pct:.1f}% from "
            "volume x price.",
        )
    if check == "dscr_average" and computed_value is not None:
        if computed_value < 1.0:
            return (
                "must_fix",
                "MUST_FIX: average DSCR below 1.0 -- over the loan's life the "
                "project's own figures do not cover its debt service.",
            )
        return (
            "should_fix",
            "SHOULD_FIX: average DSCR below the 1.25 Indian banks usually "
            "require.",
        )
    if check.startswith("dscr_stress_price_"):
        pct = check.rsplit("_", 1)[1]
        return (
            "should_fix" if pct == "10" else "good_to_fix",
            f"{'SHOULD_FIX' if pct == '10' else 'GOOD_TO_FIX'}: DSCR falls "
            f"below 1.0 if the price falls {pct}% (stress test).",
        )
    if check == "dscr_stated_differs":
        return (
            "should_fix",
            "SHOULD_FIX: the stated DSCR does not follow the standard formula "
            "(reference CAM template: \"Non-standard repayment formula\").",
        )
    if check.startswith("dscr_year_"):
        later_year_insolvent = computed_value is not None and computed_value < 1.0
        return (
            "should_fix" if later_year_insolvent else "good_to_fix",
            (
                "SHOULD_FIX: a later year's DSCR is below 1.0."
                if later_year_insolvent
                else "GOOD_TO_FIX: informational DSCR figure, no threshold breached."
            ),
        )
    if check == "fund_flow_sources_applications_balance":
        # An unbalanced fund-flow statement is a presentation defect: the gap
        # is the accumulated cash surplus left out as a closing balance. The
        # bank's reference CAM template (cam-design-reference/, page 2)
        # rates exactly this case -- sources 102.65 vs uses 62.36 lakh on a
        # DPR the bank approved -- "Good to fix: Add closing cash". Its size
        # alone never makes it more urgent.
        return (
            "good_to_fix",
            "GOOD_TO_FIX: fund-flow sources and applications do not balance; "
            "add the closing cash balance (reference CAM template, page 2).",
        )
    if stated_value in (None, 0):
        return (
            "should_fix",
            "SHOULD_FIX: mismatch found but no stated total to compute a "
            "percentage impact against.",
        )
    impact_pct = abs((computed_value or 0) - stated_value) / abs(stated_value) * 100
    if impact_pct >= thresholds.must_fix_impact_pct:
        return (
            "must_fix",
            f"MUST_FIX: {check} discrepancy is {impact_pct:.1f}% of the stated "
            f"total, at/above the {thresholds.must_fix_impact_pct:.0f}% threshold.",
        )
    return (
        "should_fix",
        f"SHOULD_FIX: {check} discrepancy is {impact_pct:.1f}% of the stated "
        f"total, below the {thresholds.must_fix_impact_pct:.0f}% threshold.",
    )


def priority_for_narrative(finding: Finding) -> tuple[str, str]:
    """Severity-based fallback for a finding with no CalculationResult
    behind it (an agent's own prose). This cannot know a dollar/percentage
    impact, so it is a coarser proxy than priority_for_calculation --
    documented limitation, see module docstring."""
    # Must-fix comes only from code checks (priority_for_calculation) and
    # checklist items (checklists.py), whose definitions are fixed. A
    # free-form finding is capped at should-fix: seen live, free-form
    # "missing document" and "critical" findings made the must-fix list
    # differ between runs of the same DPR.
    if finding.category == "missing_document" or finding.severity in (
        "critical", "high"
    ):
        return (
            "should_fix",
            f"SHOULD_FIX: free-form finding ({finding.category}, severity "
            f"{finding.severity}); must-fix comes only from code checks and "
            "checklist items.",
        )
    return (
        "good_to_fix",
        f"GOOD_TO_FIX: severity {finding.severity}, no material financial "
        "impact indicated.",
    )
