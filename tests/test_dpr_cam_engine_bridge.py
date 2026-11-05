"""Milestone 2b: wiring cam_financial_engine.py into the extraction pipeline.

Uses the same Tikamgarh golden figures as test_dpr_cam_financial_engine.py,
this time expressed as a ProjectFinancials the extraction prompt would
produce, to prove the bridge reproduces the same golden numbers through the
schema layer, not just when called directly.
"""

from decimal import Decimal

from app.services.dpr_agentic.cam_engine_bridge import run_harvester_engine
from app.services.dpr_agentic.financial_schema import (
    CostComponent,
    CrewRole,
    ExtractedValue,
    HarvesterAssumptions,
    MeansOfFinanceComponent,
    ProjectFinancials,
    YearFinancials,
)


def _tikamgarh_financials() -> ProjectFinancials:
    return ProjectFinancials(
        term_loan_amount=ExtractedValue(value=22.50, page=3, status="extracted"),
        loan_tenor_years=ExtractedValue(value=7, page=3, status="extracted"),
        loan_moratorium_years=ExtractedValue(value=0, page=3, status="extracted"),
        standard_interest_rate_pct=9.00,
        cost_of_project_components=[
            CostComponent(label="AS-930 combine harvester", amount=30.00)
        ],
        cost_of_project_stated_total=30.00,
        means_of_finance_components=[
            MeansOfFinanceComponent(label="Promoter contribution", amount=7.50),
            MeansOfFinanceComponent(label="SBI term loan", amount=22.50),
        ],
        means_of_finance_stated_total=30.00,
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
    assert fuel_findings[0].dpr_stated_value == "7.2 lakh"
    assert fuel_findings[0].recomputed_display_value == "9.12 lakh"


def test_bridge_flags_the_crew_wage_mismatch():
    _, findings = run_harvester_engine(_tikamgarh_financials())
    crew_findings = [f for f in findings if "crew" in f.title.lower()]
    assert len(crew_findings) == 1
    assert "3.3" in crew_findings[0].explanation
    assert crew_findings[0].dpr_stated_value == "2.4 lakh"
    assert crew_findings[0].recomputed_display_value == "3.3 lakh"


def test_bridge_flags_the_fund_flow_gap():
    outputs, findings = run_harvester_engine(_tikamgarh_financials())
    assert outputs.fund_flow_gap.computed_value == Decimal("40.29")
    gap_findings = [f for f in findings if "fund flow" in f.title.lower()]
    assert len(gap_findings) == 1
    assert gap_findings[0].priority == "must_fix"
    # Sources/applications is a two-sided reconciliation, not a single
    # "DPR stated this, it should be that" figure -- must not be rendered
    # as one (would misleadingly imply applications is the "correct" sources).
    assert gap_findings[0].dpr_stated_value is None
    assert gap_findings[0].recomputed_display_value is None


def test_bridge_maps_project_cost_and_means_of_finance():
    """These were already extracted by financial_schema.ProjectFinancials
    (and already used by financial_calc.py's own reconciliation checks)
    before this wiring existed -- confirms the mapping gap is closed, not
    a new extraction."""
    outputs, _ = run_harvester_engine(_tikamgarh_financials())
    assert [c.model_dump() for c in outputs.project_cost_components] == [
        {"label": "AS-930 combine harvester", "amount": "30.0"}
    ]
    assert outputs.project_cost_stated_total == "30.0"
    assert outputs.cost_reconciliation.computed_value == Decimal("0.0")

    by_label = {c.label: c for c in outputs.means_of_finance_components}
    assert by_label["SBI term loan"].source_class == "loan"
    assert by_label["Promoter contribution"].source_class == "promoter"
    assert outputs.requested_loan == "22.5"
    assert outputs.promoter_contribution == "7.5"
    assert outputs.funding_balances_cost.computed_value == Decimal("0.0")
    assert outputs.proposed_tenure_months == 84


def test_bridge_does_not_classify_an_unrecognized_finance_label_as_loan_or_promoter():
    from app.services.dpr_agentic.financial_schema import MeansOfFinanceComponent

    fin = ProjectFinancials(
        means_of_finance_components=[
            MeansOfFinanceComponent(label="State capital subsidy", amount=2.00),
        ],
        means_of_finance_stated_total=2.00,
    )
    outputs, _ = run_harvester_engine(fin)
    assert outputs.means_of_finance_components[0].source_class == "other"
    assert outputs.requested_loan is None
    assert outputs.promoter_contribution is None


def test_bridge_computes_feasible_acres_and_flags_the_mismatch():
    outputs, findings = run_harvester_engine(_tikamgarh_financials())
    # 10 acres/day x 30 operating days x 2 seasons = 600, vs. the DPR's
    # planned 1,200 -- a real, deterministic mismatch, not the design
    # reference's own 300-600 "realistic range" judgment call.
    assert outputs.feasible_acres_per_year.computed_value == Decimal("600")
    acreage_findings = [f for f in findings if "acreage" in f.title.lower()]
    assert len(acreage_findings) == 1
    assert acreage_findings[0].dpr_stated_value == "1200 acres/year"
    assert acreage_findings[0].recomputed_display_value == "600 acres/year"
    assert acreage_findings[0].priority == "must_fix"


def test_bridge_computes_debt_service_threshold_breakeven():
    outputs, _ = run_harvester_engine(_tikamgarh_financials())
    trace = outputs.breakeven_debt_service_threshold
    assert trace is not None
    assert trace.status == "COMPUTED"
    assert trace.computed_value > 0


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
