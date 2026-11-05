"""Deterministic recomputation of a DPR's own financial figures. No LLM here.

Every check compares a value computed from ProjectFinancials' own component
figures against the document's own stated total -- it never judges scheme or
policy compliance (no policy corpus is attached; see orchestrator.py's
"policy" prompt), only whether the document's numbers are internally
consistent. A mismatch is a fact a human must look at, not a verdict.
"""

from dataclasses import dataclass

from .financial_schema import ProjectFinancials
from .finding_rules import compute_dedup_key, priority_for_calculation
from .schemas import Finding

TOLERANCE = 1.0  # rupees; rounding in a DPR's own tables is routine
DSCR_TOLERANCE = 0.05  # ratio points
RATE_TOLERANCE = 0.1  # percentage points


@dataclass(frozen=True)
class CalculationResult:
    check: str
    computed_value: float | None
    stated_value: float | None
    match: bool
    discrepancy: float | None


def cost_of_project_matches_components(
    fin: ProjectFinancials,
) -> CalculationResult | None:
    if not fin.cost_of_project_components:
        return None
    computed = round(sum(c.amount for c in fin.cost_of_project_components), 2)
    stated = fin.cost_of_project_stated_total
    if stated is None:
        return None
    discrepancy = round(computed - stated, 2)
    return CalculationResult(
        "cost_of_project_total",
        computed,
        stated,
        abs(discrepancy) <= TOLERANCE,
        discrepancy,
    )


def means_of_finance_matches_components(
    fin: ProjectFinancials,
) -> CalculationResult | None:
    if not fin.means_of_finance_components:
        return None
    computed = round(sum(c.amount for c in fin.means_of_finance_components), 2)
    stated = fin.means_of_finance_stated_total
    if stated is None:
        return None
    discrepancy = round(computed - stated, 2)
    return CalculationResult(
        "means_of_finance_total",
        computed,
        stated,
        abs(discrepancy) <= TOLERANCE,
        discrepancy,
    )


def means_of_finance_balances_cost(
    fin: ProjectFinancials,
) -> CalculationResult | None:
    """The project should be fully funded: means of finance's stated total
    should equal cost of project's stated total."""
    cost = fin.cost_of_project_stated_total
    finance = fin.means_of_finance_stated_total
    if cost is None or finance is None:
        return None
    discrepancy = round(finance - cost, 2)
    return CalculationResult(
        "means_of_finance_balances_cost",
        finance,
        cost,
        abs(discrepancy) <= TOLERANCE,
        discrepancy,
    )


def dscr_per_year(fin: ProjectFinancials) -> list[CalculationResult]:
    """DSCR for every year with complete cashflow figures, using the
    standard formula (interest added back into the numerator). No pass/
    fail threshold is applied here -- that's scheme-dependent and out of
    scope; the finding built from this (see findings_from_calculations)
    flags only DSCR < 1.0, which is a universal insolvency signal, not a
    scheme pass/fail line.

    Real DPR preparers do not all use this formula (some omit the
    interest add-back, giving a materially lower number from the same
    inputs). When the document states its own DSCR (`y.stated_dscr`), it
    is compared here rather than silently overridden -- `stated_value`/
    `match`/`discrepancy` are populated so the finding can show both
    figures instead of asserting ours as the only one."""
    results = []
    for y in fin.years:
        parts = (
            y.net_profit,
            y.depreciation,
            y.interest_on_term_loan,
            y.principal_repayment,
            y.interest_repayment,
        )
        if any(p is None for p in parts):
            continue
        denominator = y.principal_repayment + y.interest_repayment
        if denominator == 0:
            continue
        dscr = round(
            (y.net_profit + y.depreciation + y.interest_on_term_loan) / denominator, 2
        )
        if y.stated_dscr is None:
            results.append(
                CalculationResult(f"dscr_year_{y.year}", dscr, None, True, None)
            )
        else:
            discrepancy = round(dscr - y.stated_dscr, 2)
            results.append(
                CalculationResult(
                    f"dscr_year_{y.year}",
                    dscr,
                    y.stated_dscr,
                    abs(discrepancy) <= DSCR_TOLERANCE,
                    discrepancy,
                )
            )
    return results


def aif_subvention_reconciles(fin: ProjectFinancials) -> CalculationResult | None:
    """Standard rate minus the stated subvention should equal the stated
    effective rate (the AIF scheme's own arithmetic -- 9% minus a 3%
    subvention is 6%, for example). All three figures are independently
    extracted, verbatim; this never infers one from the other two."""
    if (
        fin.standard_interest_rate_pct is None
        or fin.subvention_rate_pct is None
        or fin.effective_interest_rate_pct is None
    ):
        return None
    computed = round(fin.standard_interest_rate_pct - fin.subvention_rate_pct, 2)
    stated = fin.effective_interest_rate_pct
    discrepancy = round(computed - stated, 2)
    return CalculationResult(
        "aif_subvention_rate",
        computed,
        stated,
        abs(discrepancy) <= RATE_TOLERANCE,
        discrepancy,
    )


def run_all_checks(fin: ProjectFinancials) -> list[CalculationResult]:
    checks = [
        cost_of_project_matches_components(fin),
        means_of_finance_matches_components(fin),
        means_of_finance_balances_cost(fin),
        aif_subvention_reconciles(fin),
    ]
    results = [c for c in checks if c is not None]
    results.extend(dscr_per_year(fin))
    return results


def _finalize(finding: Finding, r: CalculationResult) -> Finding:
    """Attaches the Section 7 priority (computed from this very
    CalculationResult's own numbers, not a severity guess), the dedup key
    and the defect/business-risk tag. Every calculated finding is a
    document_defect by construction -- it is, by definition, a mismatch in
    the DPR's own figures, never a property of the project."""
    priority, rule = priority_for_calculation(
        check=r.check, computed_value=r.computed_value, stated_value=r.stated_value
    )
    finding.priority = priority
    finding.priority_rule = rule
    finding.finding_class = "document_defect"
    finding.dedup_key = compute_dedup_key(finding)
    return finding


def findings_from_calculations(results: list[CalculationResult]) -> list[Finding]:
    """Converts a mismatch or a notable DSCR directly into a Finding, in
    code -- never relies on an LLM choosing to mention a computed fact."""
    findings: list[Finding] = []
    for r in results:
        if r.check.startswith("dscr_year_"):
            low = r.computed_value is not None and r.computed_value < 1.0
            if r.stated_value is None:
                convention_note = (
                    " This is our own standard-formula recomputation (net profit + "
                    "depreciation + interest on term loan, divided by principal + "
                    "interest due); the document did not state its own DSCR for "
                    "this year to compare against."
                )
            elif r.match:
                convention_note = (
                    f" This matches the document's own stated DSCR of {r.stated_value}."
                )
            else:
                convention_note = (
                    f" The document itself states a DSCR of {r.stated_value} for "
                    "this year. The difference from our standard-formula figure "
                    "usually means the document's own convention does not add "
                    "interest back into the numerator -- not that either figure "
                    "is wrong. Confirm which convention the lender expects."
                )
            findings.append(
                _finalize(
                    Finding(
                        category="dscr",
                        severity="high" if low else "info",
                        title=(
                            f"{r.check.replace('_', ' ').upper()}: {r.computed_value}"
                        ),
                        explanation=(
                            "Computed from the document's own net profit, "
                            "depreciation, interest and repayment figures for this "
                            "year." + convention_note
                        ),
                        suggested_action=(
                            "DSCR below 1.0: the project's own figures show this "
                            "year's cashflow does not cover its debt service."
                            if low
                            else "No action needed from this figure alone."
                        ),
                        provenance="calculated",
                        verification="source_verified",
                    ),
                    r,
                )
            )
        elif r.check == "aif_subvention_rate":
            if not r.match:
                findings.append(
                    _finalize(
                        Finding(
                            category="interest_subvention",
                            severity="high",
                            title="AIF effective interest rate does not reconcile",
                            explanation=(
                                f"Standard rate minus the stated subvention computes "
                                f"to {r.computed_value:.2f}% p.a.; the document "
                                f"states an effective rate of {r.stated_value:.2f}% "
                                f"p.a. (difference {r.discrepancy:.2f} points). This "
                                "feeds directly into the repayment schedule and DSCR."
                            ),
                            suggested_action=(
                                "Verify the subvention percentage and effective rate "
                                "with the applicant or the scheme rules."
                            ),
                            provenance="calculated",
                            verification="source_verified",
                        ),
                        r,
                    )
                )
        elif not r.match:
            findings.append(
                _finalize(
                    Finding(
                        category="financial_calculation",
                        severity="high",
                        title=f"{r.check.replace('_', ' ').title()} does not reconcile",
                        explanation=(
                            f"Computed {r.computed_value:,.2f} from the document's "
                            f"own component figures; the document states "
                            f"{r.stated_value:,.2f} (difference {r.discrepancy:,.2f})."
                        ),
                        suggested_action="Verify these figures with the applicant.",
                        provenance="calculated",
                        verification="source_verified",
                    ),
                    r,
                )
            )
    return findings
