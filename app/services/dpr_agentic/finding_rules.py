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
    """category + affected metric (title, normalized) + source page
    references, per Section 5.1. Title is used as the "affected metric"
    proxy since agents don't emit a separate metric field; two findings
    about the same metric from different agents reliably use near-identical
    titles in practice (e.g. financial_calc.py's own titles are
    deterministic strings, and narrative agents tend to name the same
    figure the same way when quoting the same page)."""
    normalized_title = " ".join(finding.title.lower().split())
    page = finding.evidence_page if finding.evidence_page is not None else "none"
    return f"{finding.category}|{normalized_title}|{page}"


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
    if finding.category == "missing_document":
        return (
            "must_fix",
            "MUST_FIX: a document required for submission is missing.",
        )
    if finding.severity == "critical":
        return "must_fix", "MUST_FIX: severity critical."
    if finding.severity == "high":
        return (
            "should_fix",
            "SHOULD_FIX: severity high (narrative, no computed impact available).",
        )
    return (
        "good_to_fix",
        f"GOOD_TO_FIX: severity {finding.severity}, no material financial "
        "impact indicated.",
    )
