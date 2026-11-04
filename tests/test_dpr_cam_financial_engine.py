"""Milestone 2, Section 9: the deterministic financial engine.

Golden regression case: the Tikamgarh AS-930 combine-harvester DPR sampled
in cam-design-reference/LAS Customer CAM Report Template.pdf and brief
Section 18. No source DPR file for this case exists in the repository (the
brief's "[PATH TO TIKAMGARH DPR]" was never supplied -- see PROGRESS.md);
every input below is a figure the finished six-page sample report itself
states, used here as what a real extraction would have produced. Every
expected output is independently re-derivable from those same stated
figures (shown in the comments), not copied from the report's own
headline number.
"""

from decimal import Decimal

import pytest

from app.services.dpr_agentic.cam_financial_engine import (
    AmortizationYear,
    BreakEvenInputs,
    CadsDefinition,
    LoanTerms,
    accounting_breakeven,
    crew_wage_reconciliation,
    derive_tax_rate,
    dscr,
    equal_principal_amortization_schedule,
    fuel_cost_reconciliation,
    fund_flow_gap,
    operating_cashflow_breakeven,
    run_harvester_scenarios,
)

# --- Golden inputs, all stated in the Tikamgarh sample report -------------
LOAN_PRINCIPAL = Decimal("22.50")  # lakh
LOAN_RATE_PCT = Decimal("9.00")
LOAN_TENOR_YEARS = 7
PROJECT_COST = Decimal("30.00")
PROMOTER_CONTRIBUTION = Decimal("7.50")

DEPRECIATION_Y1 = Decimal(
    "4.50"
)  # lakh; "6.53L fixed cost = 4.50L dep + 2.03L interest"
PBT_STATED = Decimal("4.27")  # lakh, DPR-stated
TAX_STATED = Decimal("0.50")  # lakh, DPR-stated

ACRES_DPR_AS_WRITTEN = Decimal("1200")
FUEL_L_PER_ACRE = Decimal("8.0")
FUEL_PRICE_PER_L = Decimal("95")  # rupees
FUEL_COST_DPR_STATED = Decimal("7.20")  # lakh

CREW_WAGE_DPR_P_AND_L = Decimal("2.40")  # lakh
CREW_WAGE_PER_STAFFING = Decimal("3.30")  # lakh, Annexure II

PRICE_PER_ACRE = Decimal("1800")
RUNNING_COST_PER_ACRE_DPR = Decimal("750")
RUNNING_COST_PER_ACRE_CORRECTED = Decimal("900")

FUND_FLOW_SOURCES = Decimal("102.65")
FUND_FLOW_APPLICATIONS = Decimal("62.36")


def test_amortization_schedule_matches_the_dprs_own_repayment_table():
    """Section 18: Year-1 debt service = ₹5.24 L (principal ₹3.21 L +
    interest ₹2.03 L). Confirmed equal-principal-over-7-years with interest
    on the opening balance reproduces this exactly -- not assumed, derived:
    22.50/7 = 3.214... rounds to 3.21; 9% x 22.50 = 2.025 rounds to 2.03."""
    schedule = equal_principal_amortization_schedule(
        LoanTerms(
            principal=LOAN_PRINCIPAL,
            annual_rate_pct=LOAN_RATE_PCT,
            tenor_years=LOAN_TENOR_YEARS,
        )
    )
    year1: AmortizationYear = schedule[0]
    assert year1.principal_due == Decimal("3.21")
    assert year1.interest_due == Decimal("2.03")
    assert year1.principal_due + year1.interest_due == Decimal("5.24")
    assert len(schedule) == LOAN_TENOR_YEARS
    assert schedule[-1].closing_balance == Decimal("0.00")


def test_tax_rate_is_derived_from_the_dpr_not_assumed():
    trace = derive_tax_rate(tax_paid=TAX_STATED, profit_before_tax=PBT_STATED)
    assert trace.status == "COMPUTED"
    # 0.50 / 4.27 = 11.71% (2dp) -- not a statutory-rate assumption.
    assert trace.computed_value == pytest.approx(
        Decimal("0.1171"), abs=Decimal("0.0001")
    )
    assert "derived" in trace.formula.lower() or "derived" in str(trace.inputs)


def test_year1_dscr_pat_dep_interest_definition():
    """Section 18: 1.97 = ₹10.30 L / ₹5.24 L."""
    trace = dscr(
        net_profit=PBT_STATED - TAX_STATED,
        depreciation=DEPRECIATION_Y1,
        interest_on_term_loan=Decimal("2.03"),
        principal_due=Decimal("3.21"),
        interest_due=Decimal("2.03"),
        definition=CadsDefinition.PAT_DEP_INTEREST,
    )
    assert trace.status == "COMPUTED"
    assert trace.computed_value == Decimal("1.97")


def test_year1_dscr_pat_dep_definition_matches_the_dprs_own_convention():
    """Section 18: 1.58, the DPR's own (no interest add-back) convention."""
    trace = dscr(
        net_profit=PBT_STATED - TAX_STATED,
        depreciation=DEPRECIATION_Y1,
        interest_on_term_loan=Decimal("2.03"),
        principal_due=Decimal("3.21"),
        interest_due=Decimal("2.03"),
        definition=CadsDefinition.PAT_DEP,
    )
    assert trace.computed_value == Decimal("1.58")


def test_fuel_cost_recomputes_higher_than_the_dpr_states():
    """Section 18: 1,200 x 8.0 L/acre x ₹95/L = ₹9.12 L (DPR states ₹7.20 L)."""
    trace = fuel_cost_reconciliation(
        acres=ACRES_DPR_AS_WRITTEN,
        litres_per_acre=FUEL_L_PER_ACRE,
        price_per_litre=FUEL_PRICE_PER_L,
        dpr_stated_lakh=FUEL_COST_DPR_STATED,
    )
    assert trace.status == "COMPUTED"
    assert trace.computed_value == Decimal("9.12")


def test_crew_wages_per_staffing_exceed_the_p_and_l_figure():
    """Section 18: Annexure II staffing says ₹3.30 L; the P&L states ₹2.40 L."""
    trace = crew_wage_reconciliation(
        per_staffing_lakh=CREW_WAGE_PER_STAFFING,
        p_and_l_stated_lakh=CREW_WAGE_DPR_P_AND_L,
    )
    assert trace.computed_value == Decimal("3.30")
    assert trace.inputs.get("p_and_l_stated_lakh") == "2.40"


def test_accounting_breakeven_at_corrected_costs():
    """Section 18: ₹900/acre variable cost and ₹6.53 L fixed cost -> 726 acres."""
    trace = accounting_breakeven(
        BreakEvenInputs(
            price_per_unit=PRICE_PER_ACRE,
            variable_cost_per_unit=RUNNING_COST_PER_ACRE_CORRECTED,
            fixed_cost_total=Decimal("653000"),
            depreciation_component=Decimal("450000"),
        )
    )
    assert trace.status == "COMPUTED"
    assert trace.computed_value == Decimal("726")


def test_accounting_breakeven_at_dpr_stated_costs_is_lower():
    """DPR states 622 acres at ₹750/acre -- same ₹6.53 L fixed cost."""
    trace = accounting_breakeven(
        BreakEvenInputs(
            price_per_unit=PRICE_PER_ACRE,
            variable_cost_per_unit=RUNNING_COST_PER_ACRE_DPR,
            fixed_cost_total=Decimal("653000"),
            depreciation_component=Decimal("450000"),
        )
    )
    assert trace.computed_value == Decimal("622")


def test_operating_cashflow_breakeven_excludes_depreciation_and_is_lower():
    """Section 9: the operating cash-flow break-even must exclude
    depreciation (a non-cash fixed cost) -- it must be strictly lower than
    the accounting break-even using the same inputs."""
    inputs = BreakEvenInputs(
        price_per_unit=PRICE_PER_ACRE,
        variable_cost_per_unit=RUNNING_COST_PER_ACRE_CORRECTED,
        fixed_cost_total=Decimal("653000"),
        depreciation_component=Decimal("450000"),
    )
    cash_trace = operating_cashflow_breakeven(inputs)
    accounting_trace = accounting_breakeven(inputs)
    assert cash_trace.status == "COMPUTED"
    assert cash_trace.computed_value < accounting_trace.computed_value


def test_fund_flow_gap_is_reported_not_plugged():
    """Section 18: sources ₹102.65 L vs applications ₹62.36 L, gap ₹40.29 L.
    The gap must be surfaced as a discrepancy, never silently absorbed."""
    trace = fund_flow_gap(
        sources_total=FUND_FLOW_SOURCES, applications_total=FUND_FLOW_APPLICATIONS
    )
    assert trace.status == "COMPUTED"
    assert trace.computed_value == Decimal("40.29")
    assert "not" in trace.formula.lower() or "closing cash" in trace.formula.lower()


def test_harvester_scenarios_are_labelled_and_never_mix_cashflow_with_debt_service():
    """Section 9/19.7: DPR-as-written at 1,200 acres reproduces the DPR's
    own 1.97x; applying only the corrected fuel/crew costs at the same
    acreage reproduces 1.49x -- both independently derivable from the
    report's own stated deltas, not copied from its headline number."""
    scenarios = run_harvester_scenarios(
        acres=ACRES_DPR_AS_WRITTEN,
        pbt_dpr_as_written=PBT_STATED,
        tax_rate=derive_tax_rate(TAX_STATED, PBT_STATED).computed_value,
        depreciation=DEPRECIATION_Y1,
        interest_on_term_loan=Decimal("2.03"),
        principal_due=Decimal("3.21"),
        interest_due=Decimal("2.03"),
        fuel_cost_dpr_stated=FUEL_COST_DPR_STATED,
        fuel_cost_recomputed=Decimal("9.12"),
        crew_wage_dpr_stated=CREW_WAGE_DPR_P_AND_L,
        crew_wage_recomputed=CREW_WAGE_PER_STAFFING,
    )
    by_id = {s.scenario_id: s for s in scenarios}
    assert by_id["dpr_as_written"].is_hypothetical is False
    assert by_id["dpr_as_written"].dscr_trace.computed_value == Decimal("1.97")
    assert by_id["corrected_costs"].is_hypothetical is True
    assert by_id["corrected_costs"].dscr_trace.computed_value == Decimal("1.49")
    # No scenario's label may claim it is the DPR's own figure once costs
    # have been overridden.
    assert "corrected" in by_id["corrected_costs"].label.lower()


def test_consolidated_distinct_findings_count_for_this_case_is_13_or_fewer():
    """Section 18: 13 or fewer distinct findings, no duplicate dedup keys.

    This asserts the dedup machinery (milestone 2 part 1) on the specific
    set of defects this sample report itself lists on page 2 (6 must-fix
    items) plus the fund-flow gap and the missing quotation -- simulating
    agents restating each one multiple times, as the real pipeline does.
    """
    from app.services.dpr_agentic.finding_consolidation import consolidate
    from app.services.dpr_agentic.schemas import Finding

    page_2_defects = [
        "Acres per year assumption exceeds plausible machine throughput",
        "Year-1 capacity ramp-up not accounted for",
        "Fuel cost understated versus acres x litres x price",
        "Crew wages understated versus Annexure II staffing",
        "Running cost per acre understated versus DPR's own breakdown",
        "Break-even acres understated versus corrected running cost",
        "Dealer quotation missing for the harvester",
        "Fund flow sources exceed applications by a large, unexplained margin",
    ]
    restated_by_every_agent = [
        Finding(
            category="financial_calculation",
            severity="high",
            title=title,
            explanation=f"restated by agent variant {agent_idx}",
            evidence_page=3,
            suggested_action="Verify.",
        )
        for title in page_2_defects
        for agent_idx in range(5)  # simulates 5 of the 10 agents restating it
    ]
    merged = consolidate(restated_by_every_agent)
    assert len(merged) == len(page_2_defects)
    assert len(merged) <= 13
    keys = [f.dedup_key for f in merged]
    assert len(keys) == len(set(keys))


def test_means_of_finance_split_is_75_25():
    """Section 18: ₹22.50 L loan (75%) + ₹7.50 L promoter (25%) = ₹30.00 L."""
    total = LOAN_PRINCIPAL + PROMOTER_CONTRIBUTION
    assert total == PROJECT_COST
    assert (LOAN_PRINCIPAL / PROJECT_COST * 100).quantize(Decimal("1")) == Decimal("75")
    assert (PROMOTER_CONTRIBUTION / PROJECT_COST * 100).quantize(
        Decimal("1")
    ) == Decimal("25")


# --- Zero-value regression tests -------------------------------------------
# A page-0 citation was silently dropped elsewhere in this pipeline by a
# truthy check (`if x else` on a numeric field) rather than `is not None`.
# A 0% interest rate, a 0-year moratorium and a ₹0 computed/stated value are
# all legitimate inputs; none of them may be mistaken for "missing".


def test_zero_percent_interest_rate_produces_zero_interest_not_a_missing_value():
    schedule = equal_principal_amortization_schedule(
        LoanTerms(
            principal=Decimal("10.00"), annual_rate_pct=Decimal("0"), tenor_years=5
        )
    )
    assert schedule[0].interest_due == Decimal("0.00")
    assert schedule[0].principal_due == Decimal("2.00")


def test_zero_moratorium_years_behaves_like_no_moratorium():
    with_zero = equal_principal_amortization_schedule(
        LoanTerms(
            principal=Decimal("10.00"),
            annual_rate_pct=Decimal("9"),
            tenor_years=5,
            moratorium_years=0,
        )
    )
    without = equal_principal_amortization_schedule(
        LoanTerms(
            principal=Decimal("10.00"), annual_rate_pct=Decimal("9"), tenor_years=5
        )
    )
    assert with_zero == without


def test_dscr_computes_correctly_when_net_profit_is_zero():
    trace = dscr(
        net_profit=Decimal("0"),
        depreciation=Decimal("5.00"),
        interest_on_term_loan=Decimal("2.00"),
        principal_due=Decimal("3.00"),
        interest_due=Decimal("2.00"),
        definition=CadsDefinition.PAT_DEP_INTEREST,
    )
    assert trace.status == "COMPUTED"
    assert trace.computed_value == Decimal("1.40")


def test_fund_flow_gap_of_exactly_zero_is_computed_not_not_computable():
    trace = fund_flow_gap(
        sources_total=Decimal("50.00"), applications_total=Decimal("50.00")
    )
    assert trace.status == "COMPUTED"
    assert trace.computed_value == Decimal("0.00")


def test_tax_rate_of_zero_is_computed_not_treated_as_missing():
    trace = derive_tax_rate(tax_paid=Decimal("0"), profit_before_tax=Decimal("10.00"))
    assert trace.status == "COMPUTED"
    assert trace.computed_value == Decimal("0.0000")
