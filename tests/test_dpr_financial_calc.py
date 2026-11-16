"""Unit tests for app/services/dpr_agentic/financial_calc.py.

Pure Python, no LLM involved: every check here is deterministic arithmetic.
"""

from app.services.dpr_agentic.financial_calc import (
    aif_subvention_reconciles,
    cost_of_project_matches_components,
    dscr_per_year,
    findings_from_calculations,
    means_of_finance_balances_cost,
    means_of_finance_matches_components,
    run_all_checks,
)
from app.services.dpr_agentic.financial_schema import (
    CostComponent,
    ExtractedValue,
    MeansOfFinanceComponent,
    ProjectFinancials,
    RepaymentTableRow,
    YearFinancials,
)


def _balanced_financials() -> ProjectFinancials:
    return ProjectFinancials(
        cost_of_project_components=[
            CostComponent(label="Land", amount=200_000),
            CostComponent(label="Machinery", amount=300_000),
        ],
        cost_of_project_stated_total=500_000,
        means_of_finance_components=[
            MeansOfFinanceComponent(label="Promoter equity", amount=150_000),
            MeansOfFinanceComponent(label="Term loan", amount=350_000),
        ],
        means_of_finance_stated_total=500_000,
    )


def test_cost_of_project_matches_components_when_balanced():
    result = cost_of_project_matches_components(_balanced_financials())
    assert result.match is True
    assert result.computed_value == 500_000
    assert result.discrepancy == 0


def test_cost_of_project_flags_a_mismatch():
    fin = ProjectFinancials(
        cost_of_project_components=[CostComponent(label="Land", amount=100_000)],
        cost_of_project_stated_total=500_000,
    )
    result = cost_of_project_matches_components(fin)
    assert result.match is False
    assert result.computed_value == 100_000
    assert result.discrepancy == -400_000


def test_cost_of_project_returns_none_without_a_stated_total_to_compare():
    fin = ProjectFinancials(
        cost_of_project_components=[CostComponent(label="Land", amount=100_000)]
    )
    assert cost_of_project_matches_components(fin) is None


def test_cost_of_project_returns_none_without_any_components():
    assert cost_of_project_matches_components(ProjectFinancials()) is None


def test_means_of_finance_matches_components_when_balanced():
    result = means_of_finance_matches_components(_balanced_financials())
    assert result.match is True


def test_means_of_finance_balances_cost_when_equal():
    result = means_of_finance_balances_cost(_balanced_financials())
    assert result.match is True
    assert result.discrepancy == 0


def test_means_of_finance_does_not_balance_cost_when_underfunded():
    fin = ProjectFinancials(
        cost_of_project_stated_total=500_000,
        means_of_finance_stated_total=350_000,
    )
    result = means_of_finance_balances_cost(fin)
    assert result.match is False
    assert result.discrepancy == -150_000


def test_tolerance_absorbs_rounding_noise():
    # Amounts are INR lakhs: 0.005 lakh = Rs 500 of table rounding.
    fin = ProjectFinancials(
        cost_of_project_components=[CostComponent(label="Land", amount=32.795)],
        cost_of_project_stated_total=32.80,
    )
    assert cost_of_project_matches_components(fin).match is True


def test_tolerance_flags_a_fifty_thousand_rupee_gap():
    """0.5 lakh = Rs 50,000. The old 1.0 tolerance (mislabelled as rupees,
    but applied to lakh amounts) accepted this as a match."""
    fin = ProjectFinancials(
        cost_of_project_components=[
            CostComponent(label="Machinery", amount=30.0),
            CostComponent(label="Shed", amount=2.30),
        ],
        cost_of_project_stated_total=32.80,
    )
    result = cost_of_project_matches_components(fin)
    assert result.match is False
    assert result.discrepancy == -0.5


def _year_without_debt_service(year: int) -> YearFinancials:
    return YearFinancials(
        year=year, net_profit=3.0, depreciation=1.5, interest_on_term_loan=2.0
    )


def test_dscr_falls_back_to_the_repayment_table():
    """The repayment extraction may put the schedule in repayment_table
    instead of years[]; DSCR must still be computed from it."""
    fin = ProjectFinancials(
        years=[_year_without_debt_service(1)],
        repayment_table=[
            RepaymentTableRow(
                year=1,
                principal_due=ExtractedValue(value=3.21, page=9, status="extracted"),
                interest_due=ExtractedValue(value=2.03, page=9, status="extracted"),
            )
        ],
    )
    [result] = dscr_per_year(fin)
    assert result.check == "dscr_year_1"
    assert result.computed_value == round((3.0 + 1.5 + 2.0) / (3.21 + 2.03), 2)


def test_dscr_ignores_repayment_cells_that_were_not_extracted():
    """The table's missing interest cell is never used; interest falls back
    to the P&L's interest on term loan (2.0), not to the unextracted cell."""
    fin = ProjectFinancials(
        years=[_year_without_debt_service(1)],
        repayment_table=[
            RepaymentTableRow(
                year=1,
                principal_due=ExtractedValue(value=3.21, page=9, status="extracted"),
                interest_due=ExtractedValue(value=9.99, page=None, status="missing"),
            )
        ],
    )
    [result] = dscr_per_year(fin)
    assert result.computed_value == round((3.0 + 1.5 + 2.0) / (3.21 + 2.0), 2)


def test_dscr_needs_principal():
    year = _year_without_debt_service(1)  # no principal anywhere
    assert dscr_per_year(ProjectFinancials(years=[year])) == []


def test_dscr_uses_pnl_term_loan_interest_when_no_interest_repaid_is_stated():
    year = _year_without_debt_service(1)
    year.principal_repayment = 3.21  # interest_repayment stays None
    [result] = dscr_per_year(ProjectFinancials(years=[year]))
    assert result.computed_value == round((3.0 + 1.5 + 2.0) / (3.21 + 2.0), 2)


def test_dscr_prefers_the_year_rows_own_figures_over_the_table():
    year = _year_without_debt_service(1)
    year.principal_repayment, year.interest_repayment = 4.0, 2.0
    fin = ProjectFinancials(
        years=[year],
        repayment_table=[
            RepaymentTableRow(
                year=1,
                principal_due=ExtractedValue(value=99.0, page=9, status="extracted"),
                interest_due=ExtractedValue(value=99.0, page=9, status="extracted"),
            )
        ],
    )
    [result] = dscr_per_year(fin)
    assert result.computed_value == round(6.5 / 6.0, 2)


def test_dscr_computes_a_healthy_year():
    fin = ProjectFinancials(
        years=[
            YearFinancials(
                year=1,
                net_profit=200_000,
                depreciation=50_000,
                interest_on_term_loan=30_000,
                principal_repayment=100_000,
                interest_repayment=30_000,
            )
        ]
    )
    results = dscr_per_year(fin)
    assert len(results) == 1
    assert results[0].check == "dscr_year_1"
    assert results[0].computed_value == round(
        (200_000 + 50_000 + 30_000) / (100_000 + 30_000), 2
    )
    assert results[0].computed_value > 1.0


def test_dscr_flags_a_year_below_one_as_a_high_severity_finding():
    fin = ProjectFinancials(
        years=[
            YearFinancials(
                year=1,
                net_profit=10_000,
                depreciation=5_000,
                interest_on_term_loan=5_000,
                principal_repayment=100_000,
                interest_repayment=30_000,
            )
        ]
    )
    results = dscr_per_year(fin)
    assert results[0].computed_value < 1.0
    findings = findings_from_calculations(results)
    assert len(findings) == 1
    assert findings[0].severity == "high"
    assert findings[0].provenance == "calculated"
    assert findings[0].verification == "source_verified"


def test_dscr_skips_a_year_with_incomplete_figures():
    """Never invent a figure the document didn't state."""
    fin = ProjectFinancials(years=[YearFinancials(year=1, net_profit=10_000)])
    assert dscr_per_year(fin) == []


def test_dscr_skips_a_year_with_zero_repayment_to_avoid_division_by_zero():
    fin = ProjectFinancials(
        years=[
            YearFinancials(
                year=1,
                net_profit=10_000,
                depreciation=5_000,
                interest_on_term_loan=5_000,
                principal_repayment=0,
                interest_repayment=0,
            )
        ]
    )
    assert dscr_per_year(fin) == []


def _year_with_stated_dscr(stated_dscr: float) -> YearFinancials:
    # Standard-formula DSCR from these figures is (200_000+50_000+30_000)
    # / (100_000+30_000) = 2.15.
    return YearFinancials(
        year=1,
        net_profit=200_000,
        depreciation=50_000,
        interest_on_term_loan=30_000,
        principal_repayment=100_000,
        interest_repayment=30_000,
        stated_dscr=stated_dscr,
    )


def test_dscr_matches_the_documents_own_stated_figure():
    fin = ProjectFinancials(years=[_year_with_stated_dscr(2.15)])
    result = dscr_per_year(fin)[0]
    assert result.match is True
    assert result.stated_value == 2.15
    findings = findings_from_calculations([result])
    assert "matches the document's own stated DSCR" in findings[0].explanation


def test_a_lower_stated_dscr_is_one_should_fix_finding_per_the_bank_template():
    """Reference CAM template: "Non-standard repayment formula: Year 1 is
    1.97x, not 1.58x" is should-fix. The per-year figure stays informational;
    one summary finding carries the priority."""
    fin = ProjectFinancials(years=[_year_with_stated_dscr(1.58)])
    result = dscr_per_year(fin)[0]
    assert result.match is False
    summary, per_year = findings_from_calculations([result])
    assert summary.title == "Stated DSCR does not follow the standard formula"
    assert summary.priority == "should_fix" and summary.severity == "medium"
    assert "stated 1.58" in summary.explanation
    assert per_year.severity == "info" and per_year.priority == "good_to_fix"


def test_a_principal_only_stated_dscr_is_high_severity_and_named():
    """A DPR dividing cash accrual 13.54 (7.53 + 4.17 + 1.84) by principal
    4.13 alone states 3.28; standard 13.54 / 5.97 = 2.27. (Synthetic: the
    live DPR these figures resemble printed 4.13 as its full instalment,
    which test_a_full_instalment_row_is_the_whole_debt_service covers.)"""
    year = YearFinancials(
        year=2,
        net_profit=7.53,
        depreciation=4.17,
        principal_repayment=4.13,
        interest_repayment=1.84,
        stated_dscr=3.28,
    )
    [result] = dscr_per_year(ProjectFinancials(years=[year]))
    assert result.computed_value == 2.27 and result.note == "principal_only"
    summary = findings_from_calculations([result])[0]
    assert summary.severity == "high" and summary.priority == "should_fix"
    assert "leaves out interest" in summary.explanation


def test_aif_subvention_reconciles_when_rates_match():
    fin = ProjectFinancials(
        standard_interest_rate_pct=9.0,
        subvention_rate_pct=3.0,
        effective_interest_rate_pct=6.0,
    )
    result = aif_subvention_reconciles(fin)
    assert result.match is True
    assert result.computed_value == 6.0
    findings = [
        f
        for f in findings_from_calculations([result])
        if f.category == "interest_subvention"
    ]
    assert findings == []


def test_aif_subvention_flags_a_mismatch():
    fin = ProjectFinancials(
        standard_interest_rate_pct=9.0,
        subvention_rate_pct=3.0,
        effective_interest_rate_pct=6.5,
    )
    result = aif_subvention_reconciles(fin)
    assert result.match is False
    assert result.discrepancy == -0.5
    findings = findings_from_calculations([result])
    assert len(findings) == 1
    assert findings[0].category == "interest_subvention"
    assert findings[0].severity == "high"
    assert findings[0].provenance == "calculated"


def test_aif_subvention_returns_none_without_all_three_rates():
    assert aif_subvention_reconciles(ProjectFinancials()) is None
    assert (
        aif_subvention_reconciles(
            ProjectFinancials(standard_interest_rate_pct=9.0, subvention_rate_pct=3.0)
        )
        is None
    )


def test_run_all_checks_on_a_fully_balanced_document_yields_no_mismatch_findings():
    results = run_all_checks(_balanced_financials())
    findings = findings_from_calculations(results)
    assert all(
        f.severity != "high" for f in findings if f.category == "financial_calculation"
    )


def test_run_all_checks_on_an_empty_document_is_a_safe_no_op():
    assert run_all_checks(ProjectFinancials()) == []
    assert findings_from_calculations([]) == []


def test_findings_from_calculations_only_reports_actual_mismatches():
    """A matching check must not also produce a spurious finding."""
    results = run_all_checks(_balanced_financials())
    mismatch_findings = [
        f
        for f in findings_from_calculations(results)
        if f.category == "financial_calculation"
    ]
    assert mismatch_findings == []


def test_a_moratorium_year_with_interest_only_gets_no_dscr():
    year = YearFinancials(
        year=1,
        net_profit=1.11,
        depreciation=0.71,
        principal_repayment=0.0,
        interest_repayment=0.32,
        stated_dscr=0.0,
    )
    assert dscr_per_year(ProjectFinancials(years=[year])) == []


def test_a_full_instalment_row_is_the_whole_debt_service():
    """Seen live: "TL Installment 4.13" is principal + interest together;
    13.54 / 4.13 = 3.28 is the DPR's own, correct, figure."""
    year = YearFinancials(
        year=2,
        net_profit=7.53,
        depreciation=4.17,
        interest_on_term_loan=1.84,
        total_instalment=4.13,
        stated_dscr=3.28,
    )
    [result] = dscr_per_year(ProjectFinancials(years=[year]))
    assert (result.computed_value, result.match) == (3.28, True)
    assert not [
        f
        for f in findings_from_calculations([result])
        if f.title.startswith("Stated DSCR")
    ]
    moratorium = year.model_copy(update={"year": 1, "total_instalment": 0.0})
    assert dscr_per_year(ProjectFinancials(years=[moratorium])) == []


def test_a_balance_sheet_that_does_not_balance_is_one_finding():
    from app.services.dpr_agentic.financial_calc import (
        balance_sheet_balances,
        findings_from_calculations,
    )
    from app.services.dpr_agentic.financial_schema import (
        ProjectFinancials,
        YearFinancials,
    )

    fin = ProjectFinancials(
        years=[
            YearFinancials(year=1, total_assets=57.27, total_liabilities=86.92),
            YearFinancials(year=2, total_assets=53.57, total_liabilities=83.22),
            YearFinancials(year=3, total_assets=40.0, total_liabilities=40.0),
        ]
    )
    result = balance_sheet_balances(fin)
    assert not result.match and result.note == "1,2"
    [finding] = findings_from_calculations([result])
    assert finding.title == "Projected balance sheet does not balance"
    assert finding.priority == "must_fix"  # gap 34% of liabilities
    assert "year(s) 1, 2" in finding.explanation
    balanced = ProjectFinancials(
        years=[YearFinancials(year=1, total_assets=31.01, total_liabilities=31.01)]
    )
    assert balance_sheet_balances(balanced).match
    assert findings_from_calculations([balance_sheet_balances(balanced)]) == []
    assert balance_sheet_balances(ProjectFinancials()) is None


def _repaying(years):
    from app.services.dpr_agentic.financial_schema import (
        ProjectFinancials,
        YearFinancials,
    )

    return ProjectFinancials(
        years=[
            YearFinancials(
                year=i,
                revenue=rev,
                net_profit=np,
                depreciation=1.0,
                interest_on_term_loan=1.0,
                principal_repayment=3.0,
                interest_repayment=1.0,
            )
            for i, (rev, np) in enumerate(years, start=1)
        ]
    )


def test_average_dscr_against_the_bank_norm():
    from app.services.dpr_agentic.financial_calc import (
        dscr_average,
        findings_from_calculations,
    )

    # cash 1+1+np; debt service 4 a year.
    low = dscr_average(_repaying([(20, 2.0), (20, 2.4)]))  # (4 + 4.4) / 8
    assert (low.computed_value, low.match) == (1.05, False)
    [finding] = findings_from_calculations([low])
    assert finding.title == "Average DSCR below the 1.25 banks require"
    assert finding.priority == "should_fix"
    insolvent = dscr_average(_repaying([(20, 1.0), (20, 1.0)]))  # 0.75
    assert findings_from_calculations([insolvent])[0].priority == "must_fix"
    healthy = dscr_average(_repaying([(20, 4.0), (20, 4.0)]))  # 1.5
    assert healthy.match and findings_from_calculations([healthy]) == []
    assert dscr_average(_repaying([(20, 4.0)])) is None  # one year: no average


def test_price_stress_reports_only_the_smallest_failing_fall():
    from app.services.dpr_agentic.financial_calc import (
        dscr_stress,
        findings_from_calculations,
    )

    # Year 2: cash 7, debt 4, revenue 20 -> 10% fall: (7-2)/4 = 1.25,
    # 20% fall: (7-4)/4 = 0.75.
    results = dscr_stress(_repaying([(10, 8.0), (20, 5.0)]))
    assert [(r.check, r.computed_value, r.note) for r in results] == [
        ("dscr_stress_price_10", 1.25, "year 2"),
        ("dscr_stress_price_20", 0.75, "year 2"),
    ]
    [finding] = findings_from_calculations(results)
    assert finding.title == "Repayment fails if the price falls 20%"
    assert finding.priority == "good_to_fix"
    fragile = dscr_stress(_repaying([(40, 2.5), (40, 2.5)]))  # (4.5-4)/4
    [finding] = findings_from_calculations(fragile)
    assert finding.title == "Repayment fails if the price falls 10%"
    assert finding.priority == "should_fix"
    assert dscr_stress(_repaying([(None, 5.0)])) == []  # no revenue: no test
