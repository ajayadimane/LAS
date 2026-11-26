"""Regressions for the financial-engine review of 2026-11-25: each test pins
a defect that produced a wrong, inverted or lost finding."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.dpr_agentic.cam_engine_bridge import run_harvester_engine
from app.services.dpr_agentic.cam_financial_engine import (
    CadsDefinition,
    LoanTerms,
    debt_service_threshold_breakeven,
    equal_principal_amortization_schedule,
    run_harvester_scenarios,
)
from app.services.dpr_agentic.financial_calc import (
    calculation_versions,
    dscr_per_year,
)
from app.services.dpr_agentic.financial_schema import (
    ExtractedValue,
    HarvesterAssumptions,
    ProjectFinancials,
    RepaymentInstalment,
    RepaymentTableRow,
    StatedRatios,
    UnitEconomics,
    UnitEconomicsYear,
    YearFinancials,
    aggregate_repayment_instalments,
)
from app.services.dpr_agentic.finding_consolidation import consolidate
from app.services.dpr_agentic.finding_judge import apply_verdicts
from app.services.dpr_agentic.finding_rules import priority_for_calculation
from app.services.dpr_agentic.schedule_parser import parse_instalment_schedule
from app.services.dpr_agentic.schemas import Finding, Verdict
from app.services.dpr_agentic.unit_economics import run_unit_economics
from app.services.dpr_agentic.yearly_rows import apply_yearly_rows, parse_yearly_rows


def ev(value, page=5):
    return ExtractedValue(value=value, page=page, status="extracted")


def _pages(*texts):
    return [SimpleNamespace(page=i, text=t) for i, t in enumerate(texts, start=10)]


# --- item 10: year-1 rule matched years 10-19 -------------------------------


def test_a_dscr_below_one_in_year_twelve_is_not_a_year_one_must_fix():
    priority, rule = priority_for_calculation(
        check="dscr_year_12", computed_value=0.8, stated_value=None
    )
    assert "year-1" not in rule
    year_one, _ = priority_for_calculation(
        check="dscr_year_1", computed_value=0.8, stated_value=None
    )
    assert year_one == "must_fix"


# --- item 11: "Instalment (Principal + Interest)" read as principal ---------


def test_an_instalment_label_naming_principal_and_interest_is_the_instalment():
    page = (
        "DSCR statement\n"
        "Instalment (Principal + Interest)   7.27  7.00  6.70\n"
        "Principal Instalment                5.24  5.24  5.24\n"
    )
    rows = parse_yearly_rows(_pages(page))
    assert rows["total_instalment"].values == (7.27, 7.0, 6.7)
    assert rows["principal_repayment"].values == (5.24, 5.24, 5.24)


# --- item 12: tables printed in thousands or crores -------------------------


def test_a_dscr_page_in_thousands_is_converted_to_lakh():
    page = "DSCR statement (Rs in '000)\nTL Installment   406.68  406.68  406.68\n"
    model = ProjectFinancials(
        years=[YearFinancials(year=y, total_instalment=4.07) for y in (1, 2, 3)]
    )
    fixed = apply_yearly_rows(model, parse_yearly_rows(_pages(page)))
    assert [y.total_instalment for y in fixed.years] == [4.07, 4.07, 4.07]


def test_a_code_read_row_far_off_the_models_lakh_figures_is_dropped():
    # No legend, so the unit cannot be known; 100x the model's reading is a
    # unit misread, and the model's lakh figures are kept.
    page = "DSCR statement\nTL Installment   406.68  406.68  406.68\n"
    model = ProjectFinancials(
        years=[YearFinancials(year=y, total_instalment=4.07) for y in (1, 2, 3)]
    )
    fixed = apply_yearly_rows(model, parse_yearly_rows(_pages(page)))
    assert [y.total_instalment for y in fixed.years] == [4.07, 4.07, 4.07]


# --- item 13: losses taxed as a credit --------------------------------------


def test_a_loss_pays_no_tax_in_the_scenarios():
    scenarios = run_harvester_scenarios(
        acres=Decimal("1200"),
        pbt_dpr_as_written=Decimal("1.00"),
        tax_rate=Decimal("0.30"),
        depreciation=Decimal("4.50"),
        interest_on_term_loan=Decimal("2.03"),
        principal_due=Decimal("3.21"),
        interest_due=Decimal("2.03"),
        fuel_cost_dpr_stated=Decimal("7.20"),
        fuel_cost_recomputed=Decimal("9.12"),
        crew_wage_dpr_stated=Decimal("2.40"),
        crew_wage_recomputed=Decimal("3.30"),
    )
    corrected = next(s for s in scenarios if s.pbt < 0)
    assert corrected.pat == corrected.pbt  # no negative tax


def test_the_debt_service_break_even_needing_a_loss_is_not_taxed():
    # Depreciation 4.50 > principal 3.21: DSCR = 1 needs PAT = -1.29, a
    # loss, so no tax applies and the break-even is exact at PBT = -1.29.
    trace = debt_service_threshold_breakeven(
        price_per_unit=Decimal("1800"),
        variable_cost_per_unit=Decimal("900"),
        fixed_cost_excluding_depreciation_and_interest=Decimal(0),
        depreciation=Decimal("450000"),
        interest_on_term_loan=Decimal("203000"),
        tax_rate=Decimal("0.30"),
        principal_due=Decimal("321000"),
        interest_due=Decimal("203000"),
        definition=CadsDefinition.PAT_DEP_INTEREST,
    )
    # units = (pbt_needed + dep + interest) / contribution
    #       = (-129000 + 653000) / 900 = 582.2
    assert trace.computed_value == Decimal("582")


# --- item 14: interest of moratorium months dropped -------------------------


def test_interest_only_months_count_in_the_years_debt_service():
    rows = [
        RepaymentInstalment(
            loan_year=1, instalment=10_000, interest=10_000, principal=0
        )
        for _ in range(6)
    ] + [
        RepaymentInstalment(
            loan_year=1, instalment=60_000, interest=10_000, principal=50_000
        )
        for _ in range(6)
    ]
    fin = aggregate_repayment_instalments(
        ProjectFinancials(repayment_instalments=rows, repayment_amount_unit="rupee")
    )
    [year] = fin.repayment_table
    assert year.principal_due.value == 3.0  # 6 x 50,000
    assert year.interest_due.value == 1.2  # all 12 months, not 6


# --- item 15: direction of the sector findings ------------------------------


def _harvester(**overrides) -> ProjectFinancials:
    assumptions = dict(
        acres_per_year=ev(300),
        acres_per_day=ev(10),
        operating_days_per_season=ev(30),
        seasons_per_year=ev(2),
        fuel_litres_per_acre=ev(8.0),
        fuel_price_per_litre=ev(95),
        fuel_cost_stated=ev(5.00),  # recomputed 300 x 8 x 95 = 2.28 lakh
    )
    assumptions.update(overrides)
    return ProjectFinancials(harvester_assumptions=HarvesterAssumptions(**assumptions))


def test_planning_fewer_acres_than_the_machine_can_do_is_never_a_must_fix():
    _, findings = run_harvester_engine(_harvester())
    [acreage] = [f for f in findings if "acreage" in f.title.lower()]
    assert acreage.title == "Planned annual acreage is well below machine capacity"
    assert acreage.priority != "must_fix"


def test_a_fuel_cost_above_the_recomputation_is_cautious_not_must_fix():
    _, findings = run_harvester_engine(_harvester())
    [fuel] = [f for f in findings if "fuel" in f.title.lower()]
    assert fuel.title == "Fuel cost higher than acres x litres/acre x price"
    assert fuel.priority != "must_fix"


def test_an_overclaimed_acreage_is_still_a_must_fix():
    _, findings = run_harvester_engine(_harvester(acres_per_year=ev(1200)))
    [acreage] = [f for f in findings if "acreage" in f.title.lower()]
    assert "exceeds plausible machine throughput" in acreage.title
    assert acreage.priority == "must_fix"


# --- item 16: moratorium years ----------------------------------------------


def test_a_six_month_moratorium_repays_half_a_years_principal_in_year_one():
    schedule = equal_principal_amortization_schedule(
        LoanTerms(
            principal=Decimal("21.00"),
            annual_rate_pct=Decimal("9"),
            tenor_years=7,
            moratorium_years=Decimal("0.5"),
        )
    )
    # 21 lakh over 6.5 repaying years = 3.23 a year; half of that in year 1.
    assert schedule[0].principal_due == Decimal("1.62")
    assert schedule[1].principal_due == Decimal("3.23")
    assert schedule[-1].closing_balance == Decimal("0.00")


def test_the_engine_dscr_uses_the_first_repaying_year():
    fin = ProjectFinancials(
        term_loan_amount=ev(22.50),
        loan_tenor_years=ev(8),
        loan_moratorium_years=ev(1),
        standard_interest_rate_pct=9.00,
        years=[
            YearFinancials(year=1, net_profit=1.0, depreciation=4.5),
            YearFinancials(
                year=2, net_profit=3.77, depreciation=4.50, interest_on_term_loan=2.03
            ),
        ],
    )
    outputs, _ = run_harvester_engine(fin)
    assert outputs.debt_service_year == 2
    assert outputs.year1_principal_due == "3.21"
    # (3.77 + 4.50 + 2.03) / (3.21 + 2.03) = 1.97, not year 1's interest-only
    # ratio.
    assert outputs.dscr_pat_dep_interest.computed_value == Decimal("1.97")


# --- item 17: consolidation never loses a more severe finding ---------------


def _finding(title, *, severity="medium", **extra):
    defaults = dict(
        category="risk",
        severity=severity,
        title=title,
        explanation=title,
        suggested_action="Check it.",
        evidence_page=4,
    )
    defaults.update(extra)
    return Finding(**defaults)


def test_a_critical_finding_is_not_folded_into_a_milder_checklist_item():
    checklist = _finding(
        "Promoter's experience in the business not shown",
        check_id="RISK_PROMOTER_EXPERIENCE",
        provenance="ai_interpreted",
    )
    critical = _finding(
        "Promoter's track record below the 10% bank margin",
        severity="critical",
    )
    titles = {f.title for f in consolidate([checklist, critical])}
    assert critical.title in titles


def test_promoter_contribution_is_not_the_promoter_experience_topic():
    checklist = _finding(
        "Promoter's experience in the business not shown",
        check_id="RISK_PROMOTER_EXPERIENCE",
        provenance="ai_interpreted",
    )
    other = _finding("Promoter contribution of 2% is below the bank margin")
    assert len(consolidate([checklist, other])) == 2


def test_a_verifier_merge_keeps_the_checklist_must_fix():
    calculated = _finding(
        "Cost of project does not reconcile",
        provenance="calculated",
        priority="should_fix",
        priority_rule="SHOULD_FIX: calc",
        finding_id="F01",
    )
    checklist = _finding(
        "No quotation for the machine",
        check_id="DOC_QUOTATION",
        provenance="ai_interpreted",
        priority="must_fix",
        priority_rule="MUST_FIX: quotation",
        finding_id="F02",
    )
    kept, _ = apply_verdicts(
        [calculated, checklist],
        [Verdict(finding_id="F02", decision="merge", duplicate_of="F01")],
    )
    [merged] = kept
    assert merged.priority == "must_fix"


# --- item 18: schedule parser ------------------------------------------------


def test_a_stray_comma_in_ocr_text_does_not_crash_the_parser():
    page = (
        "Repayment schedule\n"
        "Jul-27  ,  206,560.00  14,593.17  191,966.83  1,753,788.83\n"
    )
    assert parse_instalment_schedule(_pages(page)) is None  # no crash


def test_prose_mentioning_a_year_is_not_a_year_header():
    rows = "\n".join(
        f"{m}-25  1,000.00  110.00  10.00  100.00  900.00"
        for m in ("Apr", "May", "Jun", "Jul", "Aug", "Sep")
    )
    rows2 = "\n".join(
        f"{m}-26  900.00  109.00  9.00  100.00  800.00"
        for m in ("Jan", "Feb", "Mar", "Apr", "May", "Jun")
    )
    page = (
        f"Repayment schedule\nThe machine works 30 days per Year: 9\n{rows}\n{rows2}\n"
    )
    schedule = parse_instalment_schedule(_pages(page))
    assert schedule is not None
    assert {r.loan_year for r in schedule.instalments} != {9}


# --- item 19: dashes and one-decimal DSCRs ----------------------------------


def test_a_dash_in_the_dscr_row_is_no_figure_not_zero():
    page = "DSCR statement\nD.S.C.R   -   3.28   3.46\n"
    rows = parse_yearly_rows(_pages(page))
    assert rows["stated_dscr"].values == (None, 3.28, 3.46)


def test_an_interest_only_instalment_year_gets_no_dscr():
    fin = ProjectFinancials(
        years=[
            YearFinancials(
                year=1,
                net_profit=5.0,
                depreciation=4.0,
                interest_on_term_loan=2.0,
                total_instalment=2.0,  # interest only: moratorium
                stated_dscr=0.0,
            )
        ]
    )
    assert dscr_per_year(fin) == []


def test_a_dscr_printed_to_one_decimal_allows_for_its_rounding():
    # Computed (3.77 + 4.50 + 2.03) / 4.60 = 2.24; printed "2.3".
    fin = ProjectFinancials(
        years=[
            YearFinancials(
                year=2,
                net_profit=3.77,
                depreciation=4.50,
                interest_on_term_loan=2.03,
                total_instalment=4.60,
                stated_dscr=2.3,
            )
        ]
    )
    [result] = dscr_per_year(fin)
    assert result.match is True


# --- item 20: price escalation ----------------------------------------------


def test_revenue_rebuild_applies_the_dprs_price_escalation():
    fin = ProjectFinancials(
        unit_economics=UnitEconomics(
            unit_name="acre",
            price_per_unit_rupees=ev(2000),
            years=[UnitEconomicsYear(year=y, units_sold=600) for y in (1, 2, 3)],
        ),
        years=[
            YearFinancials(year=1, revenue=12.0),
            YearFinancials(year=2, revenue=12.72),  # +6%
            YearFinancials(year=3, revenue=13.48),
        ],
        stated_ratios=StatedRatios(price_escalation_pct=ev(6)),
    )
    results, findings = run_unit_economics(fin)
    assert all(r.match for r in results if r.check.startswith("revenue_rebuild"))
    assert not [f for f in findings if f.title.startswith("Revenue")]


# --- item 21: calculation versions ------------------------------------------


def test_every_calculation_module_has_a_recorded_version():
    versions = calculation_versions()
    assert set(versions) == {
        "financial_calc",
        "cam_financial_engine",
        "unit_economics",
        "yearly_rows",
    }
    assert all(v != "1.0.0" for v in versions.values())


# --- eval of 2026-11-25 -----------------------------------------------------


def test_an_empty_table_row_does_not_block_the_transcribed_instalments():
    rows = [
        RepaymentInstalment(
            loan_year=1, instalment=222_548, interest=75_000, principal=147_548
        ),
        RepaymentInstalment(
            loan_year=1, instalment=222_548, interest=70_574, principal=151_974
        ),
    ]
    fin = aggregate_repayment_instalments(
        ProjectFinancials(
            repayment_instalments=rows,
            repayment_amount_unit="rupee",
            repayment_table=[RepaymentTableRow(year=1)],  # all values null
        )
    )
    [year] = fin.repayment_table
    assert year.principal_due.value == 3.0
    assert year.interest_due.value == 1.46


def test_a_filled_table_row_still_wins_over_the_instalments():
    fin = aggregate_repayment_instalments(
        ProjectFinancials(
            repayment_instalments=[
                RepaymentInstalment(loan_year=1, interest=1, principal=99_999)
            ],
            repayment_amount_unit="rupee",
            repayment_table=[RepaymentTableRow(year=1, principal_due=ev(2.5))],
        )
    )
    [year] = fin.repayment_table
    assert year.principal_due.value == 2.5


def test_an_instalment_equal_to_the_principal_alone_adds_the_interest():
    # ed8d3d88327b: "instalment" 4.07 = principal 4.07, interest 4.71 apart.
    fin = ProjectFinancials(
        years=[
            YearFinancials(
                year=2,
                net_profit=22.12,
                depreciation=5.09,
                interest_on_term_loan=4.12,
                principal_repayment=8.13,
                interest_repayment=4.12,
                total_instalment=8.13,
            )
        ]
    )
    [result] = dscr_per_year(fin)
    assert result.computed_value == round((22.12 + 5.09 + 4.12) / (8.13 + 4.12), 2)


def test_a_true_instalment_still_uses_the_instalment_branch():
    fin = ProjectFinancials(
        years=[
            YearFinancials(
                year=2,
                net_profit=10.0,
                depreciation=4.0,
                interest_on_term_loan=2.0,
                principal_repayment=6.0,
                total_instalment=8.0,
            )
        ]
    )
    [result] = dscr_per_year(fin)
    assert result.computed_value == 2.0


def test_every_extraction_prompt_asks_for_plain_numbers():
    import asyncio

    from app.services.dpr_agentic.financial_extraction import (
        NUMBER_FORMAT_RULE,
        extract_financial_sections,
    )

    prompts = []

    class Llm:
        async def extract_group(self, prompt, context, group):
            prompts.append(prompt)
            return ProjectFinancials()

    pages = _pages(
        "Repayment schedule\nYear Principal Interest\n",
        "Cost of project\nMeans of finance\n",
        "Projected profit and loss\nRevenue\n",
    )
    asyncio.run(extract_financial_sections(Llm(), pages, {}))
    assert prompts and all(NUMBER_FORMAT_RULE in p for p in prompts)


def test_ai_duplicates_never_re_rate_a_calculated_finding():
    # ed8d3d88327b: code rated the 50% means-of-finance gap "high"; three
    # agents' "critical" restatements merged in and flipped readiness.
    calculated = _finding(
        "Means Of Finance Total does not reconcile",
        severity="high",
        category="financial_calculation",
        provenance="calculated",
    )
    restated = _finding(
        "Means of finance total does not reconcile",
        severity="critical",
        category="financial_calculation",
        provenance="ai_interpreted",
    )
    [merged] = consolidate([calculated, restated])
    assert merged.provenance == "calculated"
    assert merged.severity == "high"
    assert merged.merged_duplicate_count == 2


# --- plain-language restatements (eval 2026-11-25, 91364956ce58) ------------


def test_two_wordings_of_one_issue_on_the_same_page_merge():
    a = _finding(
        "freight and transit costs from Bhopal are not budgeted",
        category="project_cost",
        evidence_page=27,
    )
    b = _finding(
        "transport cost from Bhopal factory is not budgeted",
        category="project_cost",
        evidence_page=27,
    )
    [merged] = consolidate([a, b])
    assert merged.merged_duplicate_count == 2


def test_sharing_only_generic_words_is_not_the_same_issue():
    from app.services.dpr_agentic.finding_consolidation import (
        same_issue_on_same_page,
    )

    a = _finding(
        "Fuel cost per year not budgeted", category="operating_costs", evidence_page=9
    )
    b = _finding(
        "Repair cost per year not budgeted", category="operating_costs", evidence_page=9
    )
    assert not same_issue_on_same_page(a, b)
    other_page = _finding(
        "transport cost from Bhopal factory is not budgeted",
        category="project_cost",
        evidence_page=3,
    )
    same = _finding(
        "freight costs from Bhopal are not budgeted",
        category="project_cost",
        evidence_page=27,
    )
    assert not same_issue_on_same_page(other_page, same)


def test_plain_restatements_join_their_checklist_and_calculated_findings():
    from app.services.dpr_agentic.checklists import TOPIC_TERMS
    from app.services.dpr_agentic.finding_consolidation import CALCULATED_TOPICS

    assert TOPIC_TERMS["FIN_WORKING_CAPITAL"].search(
        "no upfront money budgeted for initial operational expenses"
    )
    assert TOPIC_TERMS["SEC_DEMAND_EVIDENCE"].search(
        "no advance booking agreements or customer proof attached"
    )
    restated = {t.pattern: r for t, r in CALCULATED_TOPICS}
    assert restated["^stated dscr"].search(
        "debt service coverage ratio uses non-standard formula"
    )
    assert restated["^full capacity assumed"].search(
        "first year assumes full machine capacity immediately"
    )


# --- duplicates told the same way / different issues kept apart -------------


def test_one_issue_under_two_titles_merges_on_its_explanation():
    # 91364956ce58 (2026-11-25): printed as #10 and #12 of the action plan.
    a = _finding(
        "intermediate projection years missing from balance sheet and cost tables",
        category="inconsistency",
        evidence_page=20,
    )
    a.explanation = (
        "Your operating expense and balance sheet tables skip alternate "
        "years, showing figures only for 2026, 2028, 2030, and 2032. The bank "
        "needs continuous yearly financial projections to appraise loan "
        "servicing across all seven years."
    )
    b = _finding(
        "expense and balance sheet tables skip alternate years",
        category="inconsistency",
        evidence_page=20,
    )
    b.explanation = (
        "Your operating expense and balance sheet tables jump directly from "
        "2026 to 2028, 2030, and 2032. The bank requires continuous annual "
        "financial figures across every year of the loan tenure."
    )
    [merged] = consolidate([a, b])
    assert merged.merged_duplicate_count == 2


def test_filler_words_alone_do_not_merge_two_costs():
    a = _finding(
        "Fuel cost per year not budgeted", category="operating_costs", evidence_page=9
    )
    b = _finding(
        "Repair cost per year not budgeted", category="operating_costs", evidence_page=9
    )
    assert len(consolidate([a, b])) == 2


def test_the_same_title_for_different_years_stays_apart():
    a = _finding("DSCR Year 1 verified", category="dscr", evidence_page=24)
    b = _finding("DSCR Year 2 verified", category="dscr", evidence_page=24)
    assert len(consolidate([a, b])) == 2
    c = _finding(
        "Baseline operating schedules reference outdated FY 2021-22 and FY 2022-23",
        category="inconsistency",
        evidence_page=6,
    )
    d = _finding(
        "Baseline operating schedules reference outdated FY 2022-23",
        category="inconsistency",
        evidence_page=6,
    )
    assert len(consolidate([c, d])) == 1  # one title just names more years


def test_alike_explanations_under_unrelated_titles_stay_apart():
    text = (
        "The project cost of 32.30 lakh is fully covered by the term loan of "
        "24.00 lakh and the promoter margin of 8.30 lakh as stated."
    )
    a = _finding(
        "Exact alignment of project cost and means of finance",
        category="funding",
        evidence_page=4,
    )
    b = _finding(
        "Verification of promoter margin contribution",
        category="funding",
        evidence_page=4,
    )
    a.explanation = b.explanation = text
    assert len(consolidate([a, b])) == 2


# --- the verifier's re-added findings (eval 2026-11-25, 20261125T135552Z) ---


def _verifier_pair():
    from app.services.dpr_agentic.finding_judge import absorb_restatements

    checklist = _finding(
        "No proof that customers will buy your service or product",
        category="market",
        evidence_page=28,
        check_id="SEC_DEMAND_EVIDENCE",
    )
    checklist.explanation = (
        "Your DPR projects custom hiring across 1,100 acres each year but "
        "provides no farmer tie-ups, advance booking registers, or letters of "
        "intent. Without customer agreements, the bank cannot verify local "
        "demand."
    )
    return absorb_restatements, checklist


def test_a_verifier_restatement_on_the_same_page_is_absorbed():
    absorb_restatements, checklist = _verifier_pair()
    added = _finding(
        "Nobody has agreed to hire the machine yet",  # no topic word of the item
        category="market",
        evidence_page=28,
    )
    added.explanation = (
        "Your DPR targets 1,100 acres of custom hiring annually but does not "
        "provide any farmer agreements or advance bookings. The bank needs "
        "confirmation of local market demand for your service."
    )
    [kept] = absorb_restatements([checklist], [added])
    assert kept.check_id == "SEC_DEMAND_EVIDENCE"
    assert kept.merged_duplicate_count == 2


def test_an_unrelated_verifier_finding_on_the_same_page_stays():
    absorb_restatements, checklist = _verifier_pair()
    other = _finding(
        "GST on the harvester charged at the wrong rate",
        category="project_cost",
        evidence_page=28,
    )
    other.explanation = (
        "The supplier quotation applies 5% GST to the combine harvester, "
        "whereas machines under HSN 8433 attract 12%, so the cost is short."
    )
    assert len(absorb_restatements([checklist], [other])) == 2


def test_a_more_severe_verifier_finding_is_not_folded_into_a_milder_one():
    absorb_restatements, checklist = _verifier_pair()
    severe = _finding(
        "Nobody has agreed to hire the machine yet", category="market", evidence_page=28
    )
    severe.severity = "critical"
    severe.explanation = checklist.explanation
    assert len(absorb_restatements([checklist], [severe])) == 2


def test_a_verifier_finding_without_a_page_is_not_absorbed_by_page():
    absorb_restatements, checklist = _verifier_pair()
    pageless = _finding(
        "Nobody has agreed to hire the machine yet",
        category="market",
        evidence_page=None,
    )
    pageless.explanation = checklist.explanation
    assert len(absorb_restatements([checklist], [pageless])) == 2


def test_plain_restatements_of_code_findings_join_them():
    calculated = [
        ("Stated DSCR does not follow the standard formula", "dscr"),
        ("Stated DSCR does not follow the standard formula", "dscr"),
        ("Stated DSCR does not follow the standard formula", "dscr"),
        ("Means Of Finance Balances Cost does not reconcile", "financial_calculation"),
        ("Repayment fails if the price falls 20%", "dscr"),
        # 2026-11-26 run (91364956ce58): each only in one run's report.
        ("Stated DSCR does not follow the standard formula", "dscr"),
        ("Full capacity assumed from year 1", "operations"),
    ]
    restated = [
        "Reported loan repayment coverage does not match standard formulas",
        "Reported debt coverage ratio differs from standard formula",
        "Loan coverage ratio is computed without interest addback",
        "Project cost does not match total funding",
        "Loan repayment fails if service rates fall twenty percent",
        "Debt service coverage ratio is calculated incorrectly",
        "First-year targets ignore your four-month setup period",
    ]
    for (title, category), words in zip(calculated, restated, strict=True):
        code = _finding(title, category=category, evidence_page=None)
        code.provenance = "calculated"
        code.severity = "medium"
        agent = _finding(words, category="assumption", evidence_page=7)
        agent.severity = "low"
        [merged] = consolidate([code, agent])
        assert merged.provenance == "calculated", words


# --- 2026-11-26 four-DPR double run -------------------------------------


def test_a_no_tax_year_read_as_pbt_only_gets_its_net_profit():
    """91364956ce58 run 1: years 6-7 read as PBT only, so DSCR covered 5 of
    7 years; run 2 read the same figures as net profit."""
    from app.services.dpr_agentic.financial_schema import fill_net_profit_from_pbt

    fin = ProjectFinancials(
        years=[
            YearFinancials(year=1, net_profit=5.42, profit_before_tax=5.42),
            YearFinancials(year=2, net_profit=6.98, profit_before_tax=6.98),
            YearFinancials(year=6, profit_before_tax=12.32),
        ]
    )
    assert fill_net_profit_from_pbt(fin).years[2].net_profit == 12.32


def test_net_profit_from_pbt_subtracts_a_stated_tax():
    from app.services.dpr_agentic.financial_schema import fill_net_profit_from_pbt

    fin = ProjectFinancials(
        years=[YearFinancials(year=1, profit_before_tax=10.0, tax_paid=2.5)]
    )
    assert fill_net_profit_from_pbt(fin).years[0].net_profit == 7.5


def test_net_profit_is_not_guessed_when_the_dpr_charges_tax():
    from app.services.dpr_agentic.financial_schema import fill_net_profit_from_pbt

    fin = ProjectFinancials(
        years=[
            YearFinancials(year=1, net_profit=7.0, profit_before_tax=10.0),
            YearFinancials(year=2, profit_before_tax=12.0),
        ]
    )
    assert fill_net_profit_from_pbt(fin).years[1].net_profit is None


def _loan(amount, principals):
    return ProjectFinancials(
        term_loan_amount=ev(amount),
        repayment_table=[
            RepaymentTableRow(year=i, principal_due=ev(p, page=9))
            for i, p in enumerate(principals, start=1)
        ],
    )


def test_a_rounding_gap_in_the_repayment_table_is_ok():
    """e3aae76bbdce: Rs 21.95 of 22 lakh repaid, "issue" in one run and
    "ok" in the next. Code answers it now."""
    from app.services.dpr_agentic.checklists import code_checklist_answers

    [answer] = code_checklist_answers(
        "finance", _loan(22.0, [3.14] * 6 + [3.11])
    ).values()
    assert answer.status == "ok"


def test_an_unpaid_loan_balance_is_an_issue_from_code():
    from app.services.dpr_agentic.checklists import code_checklist_answers

    [answer] = code_checklist_answers("finance", _loan(22.0, [3.0] * 6)).values()
    assert answer.status == "issue" and answer.evidence_page == 9
    assert "4.00 lakh" in answer.explanation


def test_the_loan_question_is_left_to_the_agent_without_full_figures():
    from app.services.dpr_agentic.checklists import code_checklist_answers

    assert code_checklist_answers("finance", _loan(22.0, [3.0, None])) == {}
    # Negative principal is interest added to the loan: the agent reads it.
    assert code_checklist_answers("finance", _loan(22.0, [12.0, -1.0, 11.0])) == {}
    # Rupees against lakh, not an unpaid loan.
    assert code_checklist_answers("finance", _loan(2200000.0, [22.0])) == {}
    assert code_checklist_answers("sector", _loan(22.0, [22.0])) == {}


def test_a_high_restatement_on_the_same_page_joins_a_medium_checklist_item():
    """e3aae76bbdce run 2: both page 8, shown twice."""
    item = _finding(
        "Your income is not worked out step by step",
        check_id="FIN_REVENUE_BUILT_UP",
        provenance="ai_interpreted",
        evidence_page=8,
        priority="should_fix",
    )
    free = _finding(
        "Revenue does not show billing rates or acreage",
        severity="high",
        evidence_page=8,
        priority="must_fix",
    )
    [merged] = consolidate([item, free])
    assert merged.check_id == "FIN_REVENUE_BUILT_UP"
    assert merged.priority == "must_fix"  # never lowered by a merge
    free.evidence_page = 3
    assert len(consolidate([item, free])) == 2


# --- 2026-11-26 second double run ---------------------------------------


def _years(**series):
    n = max(len(v) for v in series.values())
    return [
        YearFinancials(year=i + 1, **{k: v[i] for k, v in series.items()})
        for i in range(n)
    ]


def test_tax_rising_with_profit_after_an_untaxed_first_year_is_ok():
    """e3aae76bbdce: "issue" in one run, "ok" in the next."""
    from app.services.dpr_agentic.checklists import code_checklist_answers

    fin = ProjectFinancials(
        years=_years(
            net_profit=[4.21, 5.69, 7.0, 8.27],
            tax_paid=[None, 0.35, 0.69, 1.02],
        )
    )
    assert code_checklist_answers("finance", fin)["FIN_TAX"].status == "ok"


def test_tax_falling_while_profit_rises_is_an_issue_from_code():
    from app.services.dpr_agentic.checklists import code_checklist_answers

    fin = ProjectFinancials(
        years=_years(net_profit=[5.0, 6.0, 7.0], tax_paid=[1.0, 0.5, 1.2])
    )
    answer = code_checklist_answers("finance", fin)["FIN_TAX"]
    assert answer.status == "issue" and "year 2" in answer.explanation


def test_the_tax_question_is_left_to_the_agent_when_unclear():
    from app.services.dpr_agentic.checklists import code_checklist_answers

    no_tax = ProjectFinancials(years=_years(net_profit=[5.0, 6.0]))
    gaps = ProjectFinancials(
        years=_years(net_profit=[5.0, 6.0, 7.0], tax_paid=[1.0, None, None])
    )
    assert "FIN_TAX" not in code_checklist_answers("finance", no_tax)
    assert "FIN_TAX" not in code_checklist_answers("finance", gaps)


def test_a_clean_depreciation_pattern_is_ok_from_code():
    """e3aae76bbdce: 15% reducing balance on 29.40 against a 29.30 cost."""
    from app.services.dpr_agentic.checklists import code_checklist_answers

    reducing = ProjectFinancials(
        cost_of_project_stated_total=29.3,
        years=_years(depreciation=[4.41, 3.75, 3.19, 2.71, 2.3, 1.96, 1.67]),
    )
    # 912a423f65cc: straight line, the remainder in the last year.
    straight = ProjectFinancials(
        cost_of_project_stated_total=30.0,
        years=_years(depreciation=[4.5] * 6 + [3.0]),
    )
    for fin in (reducing, straight):
        answer = code_checklist_answers("finance", fin)["FIN_DEPRECIATION"]
        assert answer.status == "ok"


def test_an_irregular_or_oversized_depreciation_is_left_to_the_agent():
    from app.services.dpr_agentic.checklists import code_checklist_answers

    irregular = ProjectFinancials(
        cost_of_project_stated_total=30.0,
        years=_years(depreciation=[4.5, 2.0, 3.9, 1.0]),
    )
    oversized = ProjectFinancials(
        cost_of_project_stated_total=10.0,
        years=_years(depreciation=[4.5] * 4),
    )
    for fin in (irregular, oversized):
        assert "FIN_DEPRECIATION" not in code_checklist_answers("finance", fin)


def test_free_form_restatements_of_code_answers_are_dropped():
    from app.services.dpr_agentic.checklists import drop_code_settled
    from app.services.dpr_agentic.schemas import AgentOutput

    out = AgentOutput(
        findings=[
            _finding("Term loan is not fully paid off in seven years"),
            _finding("Road tax is not in the project cost"),
        ]
    )
    kept = drop_code_settled(out, {"FIN_LOAN_REPAID": None, "FIN_TAX": None})
    assert [f.title for f in kept.findings] == ["Road tax is not in the project cost"]


class _FundFlowLLM:
    def __init__(self):
        self.calls = []

    async def extract_group(self, prompt, ctx, group):
        from app.services.dpr_agentic.financial_extraction import (
            FUND_FLOW_ONLY_PROMPT,
        )

        self.calls.append(prompt)
        if prompt.startswith(FUND_FLOW_ONLY_PROMPT):
            return ProjectFinancials(
                fund_flow_sources_total=ev(39.9, page=11),
                fund_flow_applications_total=ev(33.64, page=11),
            )
        return ProjectFinancials()


@pytest.mark.asyncio
async def test_missing_fund_flow_totals_are_read_from_the_fund_flow_page():
    """e3aae76bbdce: read in one run and not the next."""
    from app.services.dpr_agentic.financial_extraction import (
        FUND_FLOW_ONLY_PROMPT,
        extract_financial_sections,
    )

    llm = _FundFlowLLM()
    out, audit = await extract_financial_sections(
        llm,
        _pages("Cost of Project 28.5 lakh", "Sources and Application of Funds"),
        {},
    )
    assert out.fund_flow_sources_total.value == 39.9
    assert "fund_flow" in audit.recovered
    assert sum(p.startswith(FUND_FLOW_ONLY_PROMPT) for p in llm.calls) == 1
    llm = _FundFlowLLM()
    await extract_financial_sections(llm, _pages("Cost of Project 28.5 lakh"), {})
    assert not any(p.startswith(FUND_FLOW_ONLY_PROMPT) for p in llm.calls)
