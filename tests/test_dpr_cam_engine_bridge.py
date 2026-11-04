"""Milestone 2b: wiring cam_financial_engine.py into the extraction pipeline.

Uses the same Tikamgarh golden figures as test_dpr_cam_financial_engine.py,
this time expressed as a ProjectFinancials the extraction prompt would
produce, to prove the bridge reproduces the same golden numbers through the
schema layer, not just when called directly.
"""

from decimal import Decimal

from app.services.dpr_agentic.cam_engine_bridge import run_harvester_engine
from app.services.dpr_agentic.financial_schema import (
    CrewRole,
    ExtractedValue,
    HarvesterAssumptions,
    ProjectFinancials,
    YearFinancials,
)


def _tikamgarh_financials() -> ProjectFinancials:
    return ProjectFinancials(
        term_loan_amount=ExtractedValue(value=22.50, page=3, status="extracted"),
        loan_tenor_years=ExtractedValue(value=7, page=3, status="extracted"),
        loan_moratorium_years=ExtractedValue(value=0, page=3, status="extracted"),
        standard_interest_rate_pct=9.00,
        fund_flow_sources_total=ExtractedValue(
            value=102.65, page=12, status="extracted"
        ),
        fund_flow_applications_total=ExtractedValue(
            value=62.36, page=12, status="extracted"
        ),
        years=[
            YearFinancials(
                year=1,
                net_profit=3.77,
                profit_before_tax=4.27,
                tax_paid=0.50,
                depreciation=4.50,
                interest_on_term_loan=2.03,
            )
        ],
        harvester_assumptions=HarvesterAssumptions(
            acres_per_year=ExtractedValue(value=1200, page=4, status="extracted"),
            acres_per_day=ExtractedValue(value=10, page=19, status="extracted"),
            operating_days_per_season=ExtractedValue(
                value=30, page=19, status="extracted"
            ),
            seasons_per_year=ExtractedValue(value=2, page=4, status="extracted"),
            price_per_acre=ExtractedValue(value=1800, page=19, status="extracted"),
            running_cost_per_acre_stated=ExtractedValue(
                value=750, page=8, status="extracted"
            ),
            running_cost_per_acre_corrected=ExtractedValue(
                value=900, page=19, status="extracted"
            ),
            fuel_litres_per_acre=ExtractedValue(value=8.0, page=19, status="extracted"),
            fuel_price_per_litre=ExtractedValue(value=95, page=19, status="extracted"),
            fuel_cost_stated=ExtractedValue(value=7.20, page=8, status="extracted"),
            crew_structure=[
                CrewRole(
                    role="Operator",
                    count=1,
                    annual_wage_per_person=2.20,
                    page=20,
                    status="extracted",
                ),
                CrewRole(
                    role="Helper",
                    count=1,
                    annual_wage_per_person=1.10,
                    page=20,
                    status="extracted",
                ),
            ],
            crew_wage_total_p_and_l_stated=ExtractedValue(
                value=2.40, page=8, status="extracted"
            ),
        ),
    )


def test_bridge_reproduces_the_golden_dscr_values():
    outputs, _ = run_harvester_engine(_tikamgarh_financials())
    assert outputs.dscr_pat_dep_interest.computed_value == Decimal("1.97")
    assert outputs.dscr_pat_dep.computed_value == Decimal("1.58")


def test_bridge_reproduces_year1_debt_service():
    outputs, _ = run_harvester_engine(_tikamgarh_financials())
    assert outputs.year1_principal_due == "3.21"
    assert outputs.year1_interest_due == "2.03"


def test_bridge_flags_the_fuel_cost_mismatch():
    _, findings = run_harvester_engine(_tikamgarh_financials())
    fuel_findings = [f for f in findings if "fuel" in f.title.lower()]
    assert len(fuel_findings) == 1
    assert "9.12" in fuel_findings[0].explanation


def test_bridge_flags_the_crew_wage_mismatch():
    _, findings = run_harvester_engine(_tikamgarh_financials())
    crew_findings = [f for f in findings if "crew" in f.title.lower()]
    assert len(crew_findings) == 1
    assert "3.3" in crew_findings[0].explanation


def test_bridge_flags_the_fund_flow_gap():
    outputs, findings = run_harvester_engine(_tikamgarh_financials())
    assert outputs.fund_flow_gap.computed_value == Decimal("40.29")
    gap_findings = [f for f in findings if "fund flow" in f.title.lower()]
    assert len(gap_findings) == 1
    assert gap_findings[0].priority == "must_fix"


def test_bridge_derives_the_tax_rate():
    outputs, _ = run_harvester_engine(_tikamgarh_financials())
    assert outputs.tax_rate.computed_value == Decimal("0.1171")


def test_bridge_computes_breakeven():
    outputs, _ = run_harvester_engine(_tikamgarh_financials())
    assert outputs.breakeven_accounting.computed_value == Decimal("726")


def test_bridge_computes_scenarios():
    outputs, _ = run_harvester_engine(_tikamgarh_financials())
    by_id = {s.scenario_id: s for s in outputs.scenarios}
    assert by_id["dpr_as_written"].dscr.computed_value == Decimal("1.97")
    assert by_id["corrected_costs"].dscr.computed_value == Decimal("1.49")
    assert by_id["corrected_costs"].is_hypothetical is True


def test_findings_are_dedup_and_priority_ready():
    _, findings = run_harvester_engine(_tikamgarh_financials())
    for f in findings:
        assert f.dedup_key
        assert f.priority
        assert f.finding_class == "document_defect"


def test_bridge_degrades_gracefully_with_no_inputs():
    outputs, findings = run_harvester_engine(ProjectFinancials())
    assert findings == []
    assert outputs.dscr_pat_dep_interest is None
    assert outputs.fund_flow_gap is None
    assert outputs.scenarios == []


def test_bridge_degrades_gracefully_with_partial_inputs():
    """Missing loan terms must not block the fund-flow check, and vice versa."""
    fin = ProjectFinancials(
        fund_flow_sources_total=ExtractedValue(value=10.00, page=1, status="extracted"),
        fund_flow_applications_total=ExtractedValue(
            value=8.00, page=1, status="extracted"
        ),
    )
    outputs, findings = run_harvester_engine(fin)
    assert outputs.fund_flow_gap.computed_value == Decimal("2.00")
    assert outputs.dscr_pat_dep_interest is None
    assert len(findings) == 1


def test_a_zero_value_moratorium_and_zero_rate_are_not_treated_as_missing():
    fin = ProjectFinancials(
        term_loan_amount=ExtractedValue(value=10.0, page=1, status="extracted"),
        loan_tenor_years=ExtractedValue(value=5, page=1, status="extracted"),
        loan_moratorium_years=ExtractedValue(value=0, page=1, status="extracted"),
        standard_interest_rate_pct=0.0,
    )
    outputs, _ = run_harvester_engine(fin)
    assert outputs.year1_principal_due == "2.00"
    assert outputs.year1_interest_due == "0.00"
