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
    MeansOfFinanceComponent,
    ProjectFinancials,
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
    fin = ProjectFinancials(
        cost_of_project_components=[CostComponent(label="Land", amount=499_999.5)],
        cost_of_project_stated_total=500_000,
    )
    assert cost_of_project_matches_components(fin).match is True


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


def test_dscr_flags_a_different_stated_convention_without_treating_it_as_an_error():
    """A real-world case: some DPRs don't add interest back into the DSCR
    numerator, giving a materially different (lower) number from the same
    inputs. The difference must be explained, not flagged as a mismatch
    the way a cost/means-of-finance discrepancy is."""
    fin = ProjectFinancials(years=[_year_with_stated_dscr(1.58)])
    result = dscr_per_year(fin)[0]
    assert result.match is False
    findings = findings_from_calculations([result])
    assert len(findings) == 1
    explanation = findings[0].explanation
    assert "1.58" in explanation
    assert "not that either figure is wrong" in explanation
    # Severity is still driven by the <1.0 insolvency check, not by this
    # convention difference -- 2.15 is healthy, so this stays "info".
    assert findings[0].severity == "info"


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
