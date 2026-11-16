"""Deterministic recomputation of a DPR's own financial figures. No LLM here.

Every check compares a value computed from ProjectFinancials' own component
figures against the document's own stated total -- it never judges scheme or
policy compliance (no policy corpus is attached; see orchestrator.py's
"policy" prompt), only whether the document's numbers are internally
consistent. A mismatch is a fact a human must look at, not a verdict.
"""

from dataclasses import dataclass

from .financial_schema import ProjectFinancials, YearFinancials
from .finding_rules import compute_dedup_key, priority_for_calculation
from .schemas import Finding

# Amounts are INR lakhs (every extraction prompt converts to lakhs), so this
# is 0.01 lakh = Rs 1,000 -- enough for a DPR's own rounding. It was 1.0
# (labelled "rupees"), which silently accepted gaps of up to Rs 1,00,000.
TOLERANCE = 0.01  # lakh

DSCR_TOLERANCE = 0.05  # ratio points
RATE_TOLERANCE = 0.1  # percentage points


@dataclass(frozen=True)
class CalculationResult:
    check: str
    computed_value: float | None
    stated_value: float | None
    match: bool
    discrepancy: float | None
    # DSCR only: which formula reproduces the document's stated figure,
    # when one does ("principal_only" or "no_interest_addback").
    note: str | None = None


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


def _debt_service_for_year(
    fin: ProjectFinancials, y: YearFinancials
) -> tuple[float | None, float | None]:
    """(principal, interest) due in year `y`. Prefers the year row's own
    figures; falls back to the DPR's repayment schedule (`repayment_table`),
    which the repayment extraction may fill instead of `years[]`. Only
    schedule cells actually extracted from the document are used -- each
    figure falls back independently, never a guess."""
    principal, interest = y.principal_repayment, y.interest_repayment
    row = next((r for r in fin.repayment_table if r.year == y.year), None)
    if row is not None:
        if principal is None and row.principal_due.status == "extracted":
            principal = row.principal_due.value
        if interest is None and row.interest_due.status == "extracted":
            interest = row.interest_due.value
    if interest is None and principal is not None:
        # Interest serviced on the term loan in a year is the P&L's own
        # "interest on term loan" for that year. Seen live: a DPR whose
        # extraction carried principal 3.21 and P&L interest 1.46 but no
        # separate interest-repaid figure got no DSCR at all.
        interest = y.interest_on_term_loan
    return principal, interest


def _dscr_on_instalment(y: YearFinancials) -> list[CalculationResult]:
    """DSCR when the DPR prints the full instalment (principal + interest):
    (net profit + depreciation + interest) / instalment. Nothing for a
    moratorium year (instalment 0)."""
    add_back = (
        y.interest_on_term_loan
        if y.interest_on_term_loan is not None
        else y.interest_repayment
    )
    if None in (y.net_profit, y.depreciation, add_back) or not y.total_instalment:
        return []
    dscr = round((y.net_profit + y.depreciation + add_back) / y.total_instalment, 2)
    if y.stated_dscr is None:
        return [CalculationResult(f"dscr_year_{y.year}", dscr, None, True, None)]
    discrepancy = round(dscr - y.stated_dscr, 2)
    matched = abs(discrepancy) <= DSCR_TOLERANCE
    no_addback = round((y.net_profit + y.depreciation) / y.total_instalment, 2)
    note = (
        "no_interest_addback"
        if not matched and abs(no_addback - y.stated_dscr) <= DSCR_TOLERANCE
        else None
    )
    return [
        CalculationResult(
            f"dscr_year_{y.year}", dscr, y.stated_dscr, matched, discrepancy, note
        )
    ]


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
        if y.total_instalment is not None:
            results.extend(_dscr_on_instalment(y))
            continue
        principal, interest = _debt_service_for_year(fin, y)
        # The standard formula adds back the same interest it services. Seen
        # live: a DPR whose interest was extracted only as "interest repaid"
        # (1.84), not as the P&L's interest line, got no DSCR at all.
        add_back = (
            y.interest_on_term_loan if y.interest_on_term_loan is not None else interest
        )
        parts = (y.net_profit, y.depreciation, add_back, principal, interest)
        if any(p is None for p in parts):
            continue
        if principal == 0:
            # A moratorium year repays no principal; the DPR prints 0.00 or
            # nothing there, and a ratio over interest alone would be
            # compared against that placeholder (seen live: 6.69 vs 0.00).
            continue
        denominator = principal + interest
        if denominator == 0:
            continue
        dscr = round((y.net_profit + y.depreciation + add_back) / denominator, 2)
        if y.stated_dscr is None:
            results.append(
                CalculationResult(f"dscr_year_{y.year}", dscr, None, True, None)
            )
        else:
            discrepancy = round(dscr - y.stated_dscr, 2)
            matched = abs(discrepancy) <= DSCR_TOLERANCE
            note = None
            if not matched:
                numerator = y.net_profit + y.depreciation + add_back
                candidates = {
                    "principal_only": numerator / principal if principal else None,
                    "no_interest_addback": (y.net_profit + y.depreciation)
                    / denominator,
                }
                note = next(
                    (
                        name
                        for name, value in candidates.items()
                        if value is not None
                        and abs(round(value, 2) - y.stated_dscr) <= DSCR_TOLERANCE
                    ),
                    None,
                )
            results.append(
                CalculationResult(
                    f"dscr_year_{y.year}",
                    dscr,
                    y.stated_dscr,
                    matched,
                    discrepancy,
                    note,
                )
            )
    return results


# The usual minimum average DSCR for an Indian bank term loan. No single
# RBI figure exists; banks set it in their credit policies. Sources
# (searched 2026-11-13): "most Indian PSU and private banks require a
# minimum DSCR of 1.25x for term loans" (taxclue.in, shardaassociates.in);
# 1.25-1.5x "the standard minimum for most scheduled banks" (same); one
# bank's credit policy: 1.25 for the completed year, 1.50 for the residual
# tenor (Bank of Maharashtra credit policy excerpt, scribd.com).
DSCR_NORM_AVERAGE = 1.25
# Revenue falls of the stress test: the price drops, the volume and every
# cost stay (so no cost falls with it), and no tax saving is counted --
# deliberately conservative, as a bank's sensitivity table is.
STRESS_PRICE_FALLS_PCT = (10, 20)


def dscr_parts(fin: ProjectFinancials) -> list[tuple[int, float, float, float | None]]:
    """(year, cash available for debt service, debt service, revenue) for
    every year dscr_per_year computes a DSCR for -- same inputs, same
    skips (moratorium years, missing figures)."""
    parts = []
    for y in fin.years:
        if y.total_instalment is not None:
            add_back = (
                y.interest_on_term_loan
                if y.interest_on_term_loan is not None
                else y.interest_repayment
            )
            if (
                None in (y.net_profit, y.depreciation, add_back)
                or not y.total_instalment
            ):
                continue
            parts.append(
                (
                    y.year,
                    y.net_profit + y.depreciation + add_back,
                    y.total_instalment,
                    y.revenue,
                )
            )
            continue
        principal, interest = _debt_service_for_year(fin, y)
        add_back = (
            y.interest_on_term_loan if y.interest_on_term_loan is not None else interest
        )
        if any(
            p is None
            for p in (y.net_profit, y.depreciation, add_back, principal, interest)
        ):
            continue
        if principal == 0 or principal + interest == 0:
            continue
        parts.append(
            (
                y.year,
                y.net_profit + y.depreciation + add_back,
                principal + interest,
                y.revenue,
            )
        )
    return parts


def dscr_average(fin: ProjectFinancials) -> CalculationResult | None:
    """Average DSCR over the repayment years, as banks compute it: total
    cash available for debt service / total debt service. Compared with
    DSCR_NORM_AVERAGE."""
    parts = dscr_parts(fin)
    if len(parts) < 2:
        return None
    average = round(sum(p[1] for p in parts) / sum(p[2] for p in parts), 2)
    return CalculationResult(
        "dscr_average",
        average,
        DSCR_NORM_AVERAGE,
        average >= DSCR_NORM_AVERAGE,
        round(average - DSCR_NORM_AVERAGE, 2),
        note=f"{len(parts)} repayment years",
    )


def dscr_stress(fin: ProjectFinancials) -> list[CalculationResult]:
    """The lowest yearly DSCR if the selling price falls by each of
    STRESS_PRICE_FALLS_PCT, every cost unchanged: (cash available - fall x
    revenue) / debt service. Needs revenue for every repayment year."""
    parts = dscr_parts(fin)
    if len(parts) < 1 or any(p[3] is None for p in parts):
        return []
    results = []
    for pct in STRESS_PRICE_FALLS_PCT:
        stressed = [
            (year, round((cash - pct / 100 * revenue) / debt, 2))
            for year, cash, debt, revenue in parts
        ]
        year, lowest = min(stressed, key=lambda s: s[1])
        results.append(
            CalculationResult(
                f"dscr_stress_price_{pct}",
                lowest,
                1.0,
                lowest >= 1.0,
                round(lowest - 1.0, 2),
                note=f"year {year}",
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


def balance_sheet_balances(fin: ProjectFinancials) -> CalculationResult | None:
    """Projected total assets = total liabilities (capital included), every
    year. Both totals are read by code from the printed rows (yearly_rows),
    so the result is the same on every run. Reported for the year with the
    largest gap; `note` lists every year that does not balance."""
    years = [
        y
        for y in fin.years
        if y.total_assets is not None and y.total_liabilities is not None
    ]
    if not years:
        return None
    off = [y for y in years if abs(y.total_assets - y.total_liabilities) > TOLERANCE]
    worst = max(off or years, key=lambda y: abs(y.total_assets - y.total_liabilities))
    return CalculationResult(
        "balance_sheet_balances",
        worst.total_assets,
        worst.total_liabilities,
        not off,
        round(worst.total_assets - worst.total_liabilities, 2),
        note=",".join(str(y.year) for y in off) or None,
    )


def run_all_checks(fin: ProjectFinancials) -> list[CalculationResult]:
    checks = [
        cost_of_project_matches_components(fin),
        means_of_finance_matches_components(fin),
        means_of_finance_balances_cost(fin),
        aif_subvention_reconciles(fin),
        balance_sheet_balances(fin),
    ]
    results = [c for c in checks if c is not None]
    results.extend(dscr_per_year(fin))
    if (average := dscr_average(fin)) is not None:
        results.append(average)
    results.extend(dscr_stress(fin))
    from .financial_ratios import run_ratio_checks

    results.extend(run_ratio_checks(fin))
    return results


# Measures, not checks (financial_ratios.py): printed on the CAM's
# recomputed-figures page against the bank baselines, never a finding and
# never counted in the readiness score.
MEASURE_CHECKS = frozenset(
    {
        "project_irr",
        "payback_years",
        "debt_equity",
        "fixed_asset_coverage",
        "cash_break_even",
        "dscr_average_price_10",
    }
)


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
    _action_plan_text(finding, r)
    return finding


def _family(check: str) -> str:
    return check.rsplit("_", 1)[0] if check[-1:].isdigit() else check


# The action plan's "why it matters", per check family.
WHY_BY_CHECK = {
    "dscr_year": "Decides whether the bank sees the loan as repayable",
    "dscr_stated_differs": "The bank recomputes DSCR and will find the gap",
    "dscr_average": "Banks usually require an average DSCR of at least 1.25",
    "dscr_stress_price": "The bank tests repayment at lower prices",
    "balance_sheet_balances": "A balance sheet that does not balance is returned",
    "aif_subvention_rate": "Changes the interest and the repayment",
    "cost_of_project_total": "The bank fixes the loan on the project cost",
    "means_of_finance_total": "Funding that does not add up is queried",
    "means_of_finance_balances_cost": "The project would be under- or over-funded",
}


def _g(value: float | None) -> str | None:
    return None if value is None else f"{value:,.2f}".rstrip("0").rstrip(".")


def _action_plan_text(finding: Finding, r: CalculationResult) -> None:
    """Fills "why it matters", "your DPR" and "should be" from the
    calculation's own numbers (never overwriting what a caller set)."""
    family = _family(r.check)
    finding.why_it_matters = finding.why_it_matters or WHY_BY_CHECK.get(family)
    stated, computed = _g(r.stated_value), _g(r.computed_value)
    if family == "dscr_year" or family == "dscr_stated_differs":
        dpr = f"DSCR {stated}" if stated else None
        should = (
            "≥ 1.0"
            if r.computed_value is not None and r.computed_value < 1.0
            else f"DSCR {computed}"
        )
    elif family == "dscr_average":
        dpr, should = f"Average DSCR {computed}", f"≥ {DSCR_NORM_AVERAGE:g}"
    elif family == "dscr_stress_price":
        pct = r.check.rsplit("_", 1)[1]
        dpr, should = f"DSCR {computed} if price falls {pct}%", "≥ 1.0"
    elif family == "balance_sheet_balances":
        dpr = f"Assets {computed}, liabilities {stated}"
        should = "Assets = liabilities"
    elif family == "aif_subvention_rate":
        dpr, should = f"{stated}% effective", f"{computed}% effective"
    elif family == "means_of_finance_balances_cost":
        dpr = f"Funding {computed}, cost {stated}"
        should = "Funding = project cost"
    elif family in ("cost_of_project_total", "means_of_finance_total"):
        # Either side may be wrong (seen live: a back-ended subsidy listed
        # as extra funding made the items add to 105 against a correct 70),
        # so the fix is named, not a figure.
        dpr = f"Total {stated}; items add to {computed}"
        should = "Items that add up to the total"
    else:
        dpr, should = stated, computed
    finding.dpr_stated_value = finding.dpr_stated_value or dpr
    finding.should_be = finding.should_be or should


_DSCR_FORMULA_NOTES = {
    "principal_only": (
        "The document's figures reproduce exactly as cash accrual divided by "
        "principal alone: its debt service leaves out interest."
    ),
    "no_interest_addback": (
        "The document's figures reproduce exactly as net profit plus "
        "depreciation (interest not added back) over principal plus interest."
    ),
}


def _stated_dscr_finding(mismatched: list[CalculationResult]) -> Finding:
    """One finding for every year whose stated DSCR differs from the
    standard formula -- not one per year (a 7-year loan would otherwise put
    seven near-identical items in the customer's list). The bank's reference
    CAM template rates a non-standard DSCR as should-fix ("Year 1 is 1.97x,
    not 1.58x"); a stated figure ABOVE the standard one flatters the
    project, so it is rated high severity."""
    overstated = [r for r in mismatched if r.stated_value > r.computed_value]
    years = ", ".join(
        f"year {r.check.rsplit('_', 1)[1]}: stated {r.stated_value}, "
        f"standard {r.computed_value}"
        for r in mismatched
    )
    notes = {r.note for r in mismatched if r.note}
    formula = " ".join(_DSCR_FORMULA_NOTES[n] for n in sorted(notes))
    direction = (
        "The stated DSCR is higher than the standard formula gives, so the "
        "project looks safer on paper than a lender will compute it."
        if overstated
        else "The stated DSCR is lower than the standard formula gives."
    )
    first = mismatched[0]
    summary = CalculationResult(
        "dscr_stated_differs",
        first.computed_value,
        first.stated_value,
        False,
        first.discrepancy,
        first.note,
    )
    return _finalize(
        Finding(
            category="dscr",
            severity="high" if overstated else "medium",
            title="Stated DSCR does not follow the standard formula",
            explanation=(
                "DSCR recomputed as (net profit + depreciation + interest on "
                "term loan) / (principal + interest due) from the document's "
                f"own figures differs from the DSCR it states ({years}). "
                f"{direction} {formula}".strip()
            ),
            suggested_action=(
                "Restate the DSCR with principal plus interest in the "
                "denominator and interest added back in the numerator."
            ),
            provenance="calculated",
            verification="source_verified",
        ),
        summary,
    )


def findings_from_calculations(results: list[CalculationResult]) -> list[Finding]:
    """Converts a mismatch or a notable DSCR directly into a Finding, in
    code -- never relies on an LLM choosing to mention a computed fact."""
    findings: list[Finding] = []
    mismatched_dscr = [
        r
        for r in results
        if r.check.startswith("dscr_year_")
        and r.stated_value is not None
        and r.computed_value is not None
        and not r.match
    ]
    if mismatched_dscr:
        findings.append(_stated_dscr_finding(mismatched_dscr))
    for r in results:
        if r.check in MEASURE_CHECKS:
            continue
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
                    "this year; see the finding 'Stated DSCR does not follow "
                    "the standard formula'."
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
        elif r.check == "dscr_average":
            if not r.match:
                findings.append(
                    _finalize(
                        Finding(
                            category="dscr",
                            severity="critical" if r.computed_value < 1.0 else "high",
                            title="Average DSCR below the 1.25 banks require",
                            explanation=(
                                f"Over the {r.note}, total cash available for debt "
                                f"service covers total debt service "
                                f"{r.computed_value:g} times (standard formula, "
                                "from the DPR's own projections). Indian banks "
                                f"usually require an average DSCR of at least "
                                f"{DSCR_NORM_AVERAGE:g}."
                            ),
                            suggested_action=(
                                "Lengthen the tenor, add a moratorium, raise the "
                                "promoter contribution or reduce the loan, so the "
                                "average DSCR reaches 1.25."
                            ),
                            provenance="calculated",
                            verification="source_verified",
                        ),
                        r,
                    )
                )
        elif r.check.startswith("dscr_stress_price_"):
            pct = r.check.rsplit("_", 1)[1]
            # Only the smallest failing fall is reported: a 20% fall failing
            # adds nothing once a 10% fall already fails.
            smaller_failed = any(
                o.check.startswith("dscr_stress_price_")
                and int(o.check.rsplit("_", 1)[1]) < int(pct)
                and not o.match
                for o in results
            )
            if not r.match and not smaller_failed:
                findings.append(
                    _finalize(
                        Finding(
                            category="dscr",
                            severity="high" if pct == "10" else "medium",
                            title=f"Repayment fails if the price falls {pct}%",
                            explanation=(
                                f"With the selling price {pct}% lower and every "
                                f"cost unchanged, DSCR falls to "
                                f"{r.computed_value:g} in {r.note}: the project "
                                "could not cover its instalment from its own cash "
                                "that year."
                            ),
                            suggested_action=(
                                "Show how the instalment would be met if prices "
                                "fall (cash reserve, lower loan or longer tenor), "
                                "and add a sensitivity table to the DPR."
                            ),
                            provenance="calculated",
                            verification="source_verified",
                        ),
                        r,
                    )
                )
        elif r.check == "balance_sheet_balances":
            if not r.match:
                years = r.note or ""
                findings.append(
                    _finalize(
                        Finding(
                            category="inconsistency",
                            severity="high",
                            title="Projected balance sheet does not balance",
                            explanation=(
                                f"Total assets and total liabilities differ in "
                                f"year(s) {years.replace(',', ', ')}; the largest "
                                f"gap is {abs(r.discrepancy):,.2f} lakh (assets "
                                f"{r.computed_value:,.2f}, liabilities "
                                f"{r.stated_value:,.2f}). A balance sheet that "
                                "does not balance means an item is missing or "
                                "counted twice in the projections."
                            ),
                            suggested_action=(
                                "Rebuild the projected balance sheet so total "
                                "assets equal total liabilities every year, and "
                                "check the item causing the gap."
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
