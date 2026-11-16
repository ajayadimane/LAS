"""Phase 2 of the 2.x layout: the bank ratios recomputed in code
(financial_ratios.py), the DPR's own stated ratios (the stated_ratios
extraction group), and how cam_review prints them on the recomputed-
figures page: measures, assumptions to revise and the stress box."""

from dataclasses import asdict
from types import SimpleNamespace

import pytest

from app.services.dpr_agentic import cam_review
from app.services.dpr_agentic.cam_adapter import build_snapshot_from_analysis
from app.services.dpr_agentic.cam_render import render_cam_report_html
from app.services.dpr_agentic.financial_calc import (
    MEASURE_CHECKS,
    findings_from_calculations,
    run_all_checks,
)
from app.services.dpr_agentic.financial_extraction import (
    extract_financial_sections,
    verify_sector_pages,
)
from app.services.dpr_agentic.financial_ratios import (
    _npv,
    cash_break_even,
    debt_equity,
    dscr_average_price_fall,
    fixed_asset_coverage,
    headroom,
    payback_years,
    project_irr,
    run_ratio_checks,
)
from app.services.dpr_agentic.financial_schema import (
    CostComponent,
    ExtractedValue,
    ProjectFinancials,
    StatedRatios,
    UnitEconomics,
    UnitEconomicsYear,
    YearFinancials,
)
from app.services.dpr_agentic.schemas import AgentOutput, AnalysisResult, DocumentFact


def ev(value, page=5):
    return ExtractedValue(value=value, page=page, status="extracted")


def _fin(**extra) -> ProjectFinancials:
    """Cost 100 (machine 90 + working capital 10), loan 75 over 5 years
    (15 a year), net profit 15, depreciation 9, revenue 50 a year."""
    years = [
        YearFinancials(
            year=n,
            revenue=50,
            net_profit=15,
            profit_before_tax=15,
            depreciation=9,
            interest_on_term_loan=interest,
            principal_repayment=15,
            interest_repayment=interest,
        )
        for n, interest in zip(range(1, 6), (6, 5, 4, 3, 2), strict=True)
    ]
    return ProjectFinancials(
        cost_of_project_components=[
            CostComponent(label="Machinery", amount=90),
            CostComponent(label="Margin money for working capital", amount=10),
        ],
        cost_of_project_stated_total=100,
        term_loan_amount=ev(75),
        loan_tenor_years=ev(7),
        effective_interest_rate_pct=9,
        years=years,
        **extra,
    )


# --- The ratios ----------------------------------------------------------------


def test_irr_is_the_rate_that_returns_the_project_cost():
    r = project_irr(_fin())
    flows = [-100, 30, 29, 28, 27, 26]
    assert abs(_npv(r.computed_value / 100, flows)) < 0.01
    assert r.note == "5 years, no terminal value" and r.match


def test_irr_needs_three_years_and_compares_the_dpr_figure():
    short = _fin()
    short.years = short.years[:2]
    assert project_irr(short) is None
    stated = _fin(stated_ratios=StatedRatios(irr_pct=ev(25)))
    r = project_irr(stated)
    assert r.stated_value == 25 and not r.match


def test_payback_interpolates_and_reports_beyond_the_projection():
    assert payback_years(_fin()).computed_value == 4.2  # 4 + 4/24
    slow = _fin(stated_ratios=StatedRatios(payback_years=ev(4)))
    for y in slow.years:
        y.net_profit = 5
    r = payback_years(slow)
    assert r.computed_value is None and r.note == "beyond 5 years"
    assert not r.match  # the DPR claims 4 years


def test_debt_equity_counts_everything_but_the_loan_as_equity():
    r = debt_equity(_fin(stated_ratios=StatedRatios(debt_equity=ev(3))))
    assert (r.computed_value, r.match) == (3.0, True)


def test_fixed_asset_coverage_is_the_lowest_over_the_loan():
    r = fixed_asset_coverage(_fin())
    # Working capital is not a fixed asset: 90 / 75 at the start.
    assert (r.computed_value, r.note) == (1.2, "at start")
    fast = _fin()
    for y in fast.years:
        y.depreciation = 20  # assets written down faster than the loan
    low = fixed_asset_coverage(fast)
    assert low.computed_value < 1.2 and low.note.startswith("year")


def test_cash_break_even_from_unit_economics():
    fin = _fin(
        unit_economics=UnitEconomics(
            unit_name="unit",
            capacity_per_year=ev(10000),
            price_per_unit_rupees=ev(1000),
            variable_cost_per_unit_rupees=ev(300),
            years=[UnitEconomicsYear(year=1, utilisation_pct=50, page=5)],
        )
    )
    # Cash costs 50-15-9-6 = 20, variable 15, fixed 5; contribution at full
    # capacity 50 x 0.7 / 0.5 = 70.
    assert cash_break_even(fin).computed_value == 7.1
    assert cash_break_even(_fin()) is None  # no variable cost per unit


def test_stress_average_and_headroom():
    fin = _fin()
    # Cash 140, debt 95, revenue 250: (140 - 25) / 95.
    assert dscr_average_price_fall(fin).computed_value == 1.21
    assert headroom(fin) == {"price_fall_pct": 14.2, "cost_rise_pct": 32.3}


def test_ratios_are_measures_never_findings():
    results = run_all_checks(_fin(stated_ratios=StatedRatios(irr_pct=ev(40))))
    names = {r.check for r in results}
    assert MEASURE_CHECKS & names >= {"project_irr", "debt_equity"}
    findings = findings_from_calculations(
        [r for r in results if r.check in MEASURE_CHECKS]
    )
    assert findings == []
    assert run_ratio_checks(ProjectFinancials()) == []


# --- On the report ---------------------------------------------------------------


def _result(fin: ProjectFinancials, scheme: str | None = None) -> AnalysisResult:
    outputs = {}
    if scheme:
        outputs["document"] = AgentOutput(
            document_facts=[
                DocumentFact(
                    field="scheme_claimed",
                    value=scheme,
                    page=1,
                    quote=scheme,
                    verified=True,
                )
            ]
        )
    return AnalysisResult(
        service_request_id="SR",
        wallet_document_id="W",
        document_sha256="a" * 64,
        status="completed",
        agent_outputs=outputs,
        findings=[],
        unresolved_questions=[],
        extracted_financials=fin.model_dump(mode="json"),
        calculations=[asdict(c) for c in run_all_checks(fin)],
    )


def test_recomputed_page_fills_every_ratio_row():
    fin = _fin(stated_ratios=StatedRatios(irr_pct=ev(14.5), debt_equity=ev(3)))
    rows = {m.measure: m for m in cam_review.financial_review(_result(fin))}
    irr = rows["Project IRR (%)"]
    assert irr.dpr_value == "14.5" and irr.baseline == "> 9% (your loan rate)"
    assert irr.status == "meets"
    assert rows["Debt : equity"].recomputed == "3.00 : 1"
    assert rows["Debt : equity"].status == "meets"
    assert rows["Fixed asset coverage"].recomputed == "1.20 (at start)"
    assert rows["Fixed asset coverage"].status == "below"
    payback = rows["Payback period (years)"]
    assert (payback.recomputed, payback.status) == ("4.2", "meets")
    assert payback.baseline == "< loan tenure (7 yrs)"
    assert rows["Cash break-even (% capacity)"].status == "pending"


def test_debt_equity_baseline_follows_the_scheme_minimum():
    fin = _fin()
    fin.term_loan_amount = ev(85)  # 85 : 15 = 5.67
    plain = {m.measure: m for m in cam_review.financial_review(_result(fin))}
    assert plain["Debt : equity"].status == "below"
    pmfme = {m.measure: m for m in cam_review.financial_review(_result(fin, "PMFME"))}
    assert pmfme["Debt : equity"].baseline == "≤ 9:1 (10% own share)"
    assert pmfme["Debt : equity"].status == "meets"


def test_assumptions_compare_with_the_references():
    fin = _fin(
        unit_economics=UnitEconomics(
            unit_name="acre",
            years=[
                UnitEconomicsYear(year=1, utilisation_pct=95, page=5),
                UnitEconomicsYear(year=2, utilisation_pct=65, page=5),
            ],
        ),
        stated_ratios=StatedRatios(
            price_escalation_pct=ev(5),
            cost_escalation_pct=ev(3),
            depreciation_method="SLM",
            depreciation_rate_pct=ev(25),
        ),
    )
    fin.benchmark_used = {
        "reference": {
            "price": {
                "dpr": 2500,
                "low": 1400,
                "high": 2200,
                "unit": "acre",
                "source": "LAS benchmark, Self-propelled combine",
            }
        }
    }
    rows = {a.assumption: a for a in cam_review.assumption_review(_result(fin))}
    price = rows["Selling price"]
    assert price.dpr_value == "₹ 2,500 per acre" and price.flagged
    assert price.suggested == "₹ 2,200 per acre"
    assert price.reference.startswith("₹ 1,400–2,200 per acre")
    util = rows["Capacity utilisation, Yr 1–3"]
    assert util.dpr_value == "95% · 65% · —" and util.flagged
    assert util.suggested == "60% · 65% · —"
    cost = rows["Input cost escalation"]
    assert cost.flagged and cost.suggested == "5% p.a., as prices"
    dep = rows["Depreciation method / rate"]
    assert dep.dpr_value == "SLM 25%" and dep.flagged
    assert rows["Interest rate"].dpr_value == "9%"
    assert rows["Yield / output per unit"].reference.startswith("No benchmark")


def test_stress_box_names_the_most_sensitive_assumption():
    stress = cam_review.stress_test(_result(_fin()))
    assert float(stress.average_dscr) == 1.21
    assert stress.most_sensitive.startswith("selling price (a 14.2% fall")


def test_the_page_prints_the_assumptions_and_stress():
    result = _result(_fin(stated_ratios=StatedRatios(cost_escalation_pct=ev(4))))
    snap = build_snapshot_from_analysis(
        result, report_id="r", report_version=1, analysis_run_id="run"
    )
    html = render_cam_report_html(snap, result=result)
    assert "4% p.a." in html and "RBI inflation target" in html
    assert "average DSCR becomes 1.21" in html
    assert "Most sensitive assumption: selling price" in html


def test_ratio_measures_do_not_move_the_readiness_score():
    fin = _fin()
    with_ratios = _result(fin)
    without = with_ratios.model_copy(
        update={
            "calculations": [
                c for c in with_ratios.calculations if c["check"] not in MEASURE_CHECKS
            ]
        }
    )
    assert cam_review.readiness_score(with_ratios) == cam_review.readiness_score(
        without
    )


# --- Extraction ----------------------------------------------------------------------


class _GroupLLM:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    async def extract_group(self, prompt, ctx, group):
        self.calls.append(group)
        return self.answers.get(group, ProjectFinancials())


@pytest.mark.asyncio
async def test_stated_ratios_group_runs_when_the_dpr_prints_ratios():
    pages = [
        SimpleNamespace(
            page=1, text="Cost of project 100 means of finance term loan 75"
        ),
        SimpleNamespace(page=2, text="Financial indicators: Project IRR: 18.5 %"),
    ]
    llm = _GroupLLM(
        {
            "stated_ratios": ProjectFinancials(
                stated_ratios=StatedRatios(irr_pct=ev(18.5, 2))
            )
        }
    )
    output, audit = await extract_financial_sections(llm, pages, {})
    assert "stated_ratios" in audit.attempted
    assert output.stated_ratios.irr_pct.value == 18.5
    plain = _GroupLLM({})
    await extract_financial_sections(
        plain, [SimpleNamespace(page=1, text="Irrigation pump cost of project 5")], {}
    )
    assert "stated_ratios" not in plain.calls  # "irrigation" is not an IRR


def test_stated_ratios_must_appear_on_their_page():
    fin = ProjectFinancials(
        stated_ratios=StatedRatios(irr_pct=ev(18.5, 2), payback_years=ev(4.5, 2))
    )
    pages = [SimpleNamespace(page=2, text="IRR 18.50% ; payback 4 years 6 months")]
    ratios = verify_sector_pages(fin, pages).stated_ratios
    assert ratios.irr_pct.value == 18.5
    assert ratios.payback_years.status == "missing"
