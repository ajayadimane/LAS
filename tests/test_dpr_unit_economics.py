"""M3: sector-neutral unit economics (unit_economics.py), its extraction
(optional groups, page verification) and the CAM's operational assumptions."""

from types import SimpleNamespace

import pytest

from app.services.dpr_agentic.cam_adapter import build_snapshot_from_analysis
from app.services.dpr_agentic.financial_extraction import (
    extract_financial_sections,
    figure_on_page,
    verify_unit_economics_pages,
)
from app.services.dpr_agentic.financial_schema import (
    ExtractedValue,
    ProjectFinancials,
    UnitEconomics,
    UnitEconomicsYear,
    YearFinancials,
    normalize_year_numbers,
)
from app.services.dpr_agentic.gemini import GeminiAgentError
from app.services.dpr_agentic.schemas import AnalysisResult
from app.services.dpr_agentic.unit_economics import run_unit_economics


def ev(value, page=5):
    return ExtractedValue(value=value, page=page, status="extracted")


def _fin(*, years=(), revenue=None, **ue) -> ProjectFinancials:
    defaults = dict(
        unit_name="acre", capacity_per_day=ev(10), operating_days_per_year=ev(120)
    )
    defaults.update(ue)
    return ProjectFinancials(
        unit_economics=UnitEconomics(years=list(years), **defaults),
        years=[YearFinancials(year=y, revenue=r) for y, r in (revenue or {}).items()],
    )


def _by_check(findings):
    return {f.priority_rule.split(":")[0]: f for f in findings}


def test_planned_volume_above_capacity_is_a_must_fix():
    fin = _fin(years=[UnitEconomicsYear(year=1, units_sold=1500, page=6)])
    results, [finding] = run_unit_economics(fin)
    assert results[0].check == "units_exceed_capacity"
    assert results[0].computed_value == 1200  # 10 acres/day x 120 days
    assert finding.priority == "must_fix" and finding.provenance == "calculated"
    assert finding.evidence_page == 6
    assert "1200" in finding.explanation


def test_the_harvester_acreage_check_replaces_the_capacity_check():
    fin = _fin(years=[UnitEconomicsYear(year=2, units_sold=1500)])
    assert run_unit_economics(fin, acreage_checked=True)[1] == []


def test_full_capacity_in_year_one_is_must_fix_and_a_ramp_up_is_not():
    full = _fin(years=[UnitEconomicsYear(year=1, utilisation_pct=100)])
    [finding] = run_unit_economics(full)[1]
    assert finding.title == "Full capacity assumed from year 1"
    assert finding.priority == "must_fix"
    ramp = _fin(years=[UnitEconomicsYear(year=1, utilisation_pct=70)])
    assert run_unit_economics(ramp)[1] == []


def test_utilisation_is_derived_from_units_and_capacity():
    fin = _fin(years=[UnitEconomicsYear(year=1, units_sold=1140)])  # 95% of 1200
    [finding] = run_unit_economics(fin)[1]
    assert "95%" in finding.explanation


def test_revenue_above_volume_x_price_is_one_must_fix_finding():
    fin = _fin(
        price_per_unit_rupees=ev(2000),
        years=[UnitEconomicsYear(year=y, units_sold=600) for y in (1, 2)],
        revenue={1: 14.4, 2: 12.0},  # rebuilt 600 x 2000 = 12.0 lakh
    )
    results, findings = run_unit_economics(fin)
    rebuilt = {r.check: r.match for r in results if r.check.startswith("revenue")}
    assert rebuilt == {"revenue_rebuild_year_1": False, "revenue_rebuild_year_2": True}
    [finding] = findings
    assert finding.title == "Revenue does not match volume x price"
    assert finding.priority == "must_fix" and finding.severity == "high"
    assert "stated 14.4, rebuilt 12" in finding.explanation


def test_revenue_below_volume_x_price_is_should_fix():
    fin = _fin(
        price_per_unit_rupees=ev(2000),
        years=[UnitEconomicsYear(year=1, units_sold=600)],
        revenue={1: 10.0},
    )
    [finding] = run_unit_economics(fin)[1]
    assert finding.priority == "should_fix" and finding.severity == "medium"


def test_price_below_variable_cost_is_must_fix():
    fin = _fin(price_per_unit_rupees=ev(900), variable_cost_per_unit_rupees=ev(950))
    [finding] = run_unit_economics(fin)[1]
    assert finding.priority == "must_fix"
    assert "loses 50 rupees" in finding.explanation


def test_an_asset_idle_most_of_the_year_is_should_fix():
    fin = _fin(operating_days_per_year=ev(60))
    [finding] = run_unit_economics(fin)[1]
    assert finding.title == "Asset idle most of the year"
    assert finding.priority == "should_fix"
    assert "about 10 months" in finding.explanation


def test_no_unit_economics_means_no_checks():
    assert run_unit_economics(ProjectFinancials()) == ([], [])


def test_figures_must_appear_on_their_cited_page():
    pages = {5: "Rate per acre Rs. 2,000. Capacity 1,00,000 litres. Days 120.0"}
    assert figure_on_page(2000, 5, pages)
    assert figure_on_page(100000, 5, pages)
    assert figure_on_page(120, 5, pages)
    assert not figure_on_page(200, 5, pages)
    assert not figure_on_page(2000, 6, pages)


def test_unverifiable_figures_are_dropped_not_trusted():
    fin = _fin(
        price_per_unit_rupees=ev(2500, page=5),
        years=[
            UnitEconomicsYear(year=1, utilisation_pct=100, page=5),
            UnitEconomicsYear(year=2, utilisation_pct=80, page=5),
        ],
    )
    pages = [SimpleNamespace(page=5, text="10 acres per day, 120 days, year 2: 80%")]
    fixed = verify_unit_economics_pages(fin, pages)
    ue = fixed.unit_economics
    assert ue.price_per_unit_rupees.status == "missing"
    assert ue.capacity_per_day.value == 10
    assert [y.year for y in ue.years] == [2]


def test_calendar_years_in_unit_economics_are_renumbered():
    fin = _fin(
        years=[
            UnitEconomicsYear(year=2027, utilisation_pct=80),
            UnitEconomicsYear(year=2026, utilisation_pct=60),
        ]
    )
    years = normalize_year_numbers(fin).unit_economics.years
    assert {y.year: y.utilisation_pct for y in years} == {2: 80, 1: 60}


PAGES = [
    SimpleNamespace(
        page=1, text="Cost of Project 30 lakh. Means of Finance: term loan 22.5"
    ),
    SimpleNamespace(
        page=2, text="Repayment Schedule year 1 principal 3.21 interest 2.03"
    ),
    SimpleNamespace(page=3, text="Profit and Loss: revenue 12 net profit 4.27"),
    SimpleNamespace(
        page=4, text="Technical: harvester covers 10 acre per day, 120 days"
    ),
]


class _GroupLLM:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    async def extract_group(self, prompt, ctx, group):
        self.calls.append(group)
        answer = self.answers.get(group, ProjectFinancials())
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.mark.asyncio
async def test_harvester_and_unit_economics_groups_run_and_merge():
    llm = _GroupLLM(
        {
            "unit_economics": ProjectFinancials(
                unit_economics=UnitEconomics(
                    unit_name="acre", capacity_per_day=ev(10, page=4)
                )
            ),
            "harvester": ProjectFinancials.model_validate(
                {
                    "harvester_assumptions": {
                        "acres_per_day": {"value": 10, "page": 4, "status": "extracted"}
                    }
                }
            ),
        }
    )
    output, audit = await extract_financial_sections(llm, PAGES, {})
    assert {"unit_economics", "harvester"} <= set(audit.attempted)
    assert llm.calls.count("unit_economics") == llm.calls.count("harvester") == 1
    assert output.unit_economics.capacity_per_day.value == 10
    assert output.harvester_assumptions.acres_per_day.value == 10
    assert "unit_economics" not in audit.empty + audit.failed


@pytest.mark.asyncio
async def test_harvester_group_needs_a_harvester_dpr_and_failures_are_quiet():
    pages = PAGES[:3] + [SimpleNamespace(page=4, text="Dairy: 200 litres per day")]
    pages[3].text += " capacity utilization 70%"
    llm = _GroupLLM({"unit_economics": GeminiAgentError("boom")})
    _, audit = await extract_financial_sections(llm, pages, {})
    assert "harvester" not in llm.calls
    assert "unit_economics" not in audit.failed


def test_snapshot_carries_operational_assumptions():
    fin = _fin(
        price_per_unit_rupees=ev(2000),
        variable_cost_per_unit_rupees=ev(1500),
        years=[UnitEconomicsYear(year=1, utilisation_pct=70)],
    )
    snap = build_snapshot_from_analysis(
        AnalysisResult(
            service_request_id="t",
            wallet_document_id="d",
            document_sha256="a" * 64,
            status="completed",
            agent_outputs={},
            findings=[],
            unresolved_questions=[],
            extracted_financials=fin.model_dump(mode="json"),
        ),
        report_id="r",
        report_version=1,
        analysis_run_id="r",
    )
    oa = snap.operational_assumptions
    assert oa.assumptions["Full capacity per year (computed)"] == "1200 acres"
    assert oa.assumptions["Contribution per unit (computed)"].startswith(
        "500 rupees/acre"
    )
    assert oa.assumptions["Utilisation year 1"] == "70%"
    assert oa.classification == "recomputed"


def test_year_one_volume_is_implied_from_revenue_when_not_printed():
    """Seen live: 1200 acres, Rs 1800/acre, year-1 revenue 21.6 lakh and no
    utilisation row -- full planned volume from day one (template #4)."""
    fin = _fin(
        capacity_per_day=ExtractedValue(),
        operating_days_per_year=ExtractedValue(),
        capacity_per_year=ev(1200),
        price_per_unit_rupees=ev(1800),
        revenue={1: 21.6, 2: 22.68},
    )
    results, [finding] = run_unit_economics(fin)
    assert finding.title == "Full capacity assumed from year 1"
    assert "implied" in finding.explanation and "1200 acres" in finding.explanation
    # Implied volume never feeds the revenue rebuild (it would be circular).
    assert not [r for r in results if r.check.startswith("revenue_rebuild")]


def test_harvester_acres_are_the_volume_target_when_capacity_is_not_filed():
    """Seen live: the model filed 1200 planned acres as capacity, as units
    sold, or nowhere, run to run; the harvester group read it every time."""
    from app.services.dpr_agentic.financial_schema import HarvesterAssumptions

    base = dict(
        capacity_per_day=ExtractedValue(),
        operating_days_per_year=ExtractedValue(),
        price_per_unit_rupees=ev(1800),
        revenue={1: 21.6},
    )
    harvester = HarvesterAssumptions(acres_per_year=ev(1200), price_per_acre=ev(1800))
    for fin in (
        _fin(**base),
        _fin(years=[UnitEconomicsYear(year=1, units_sold=1200)], **base),
    ):
        fin.harvester_assumptions = harvester
        titles = [f.title for f in run_unit_economics(fin)[1]]
        assert titles == ["Full capacity assumed from year 1"]
    # The generic group returned nothing: built from the harvester figures.
    only_harvester = ProjectFinancials(
        harvester_assumptions=harvester, years=[YearFinancials(year=1, revenue=21.6)]
    )
    assert [f.title for f in run_unit_economics(only_harvester)[1]] == [
        "Full capacity assumed from year 1"
    ]


@pytest.mark.asyncio
async def test_polyhouse_group_runs_only_for_a_polyhouse_dpr():
    from app.services.dpr_agentic.financial_schema import PolyhouseAssumptions

    llm = _GroupLLM(
        {
            "polyhouse": ProjectFinancials(
                polyhouse_assumptions=PolyhouseAssumptions(
                    crop="Dutch Rose", area_sq_m=ev(4000, page=4)
                )
            )
        }
    )
    pages = PAGES[:3] + [
        SimpleNamespace(page=4, text="Technical: Polyhouse (NVPH) of 4,000 sq.mt")
    ]
    output, audit = await extract_financial_sections(llm, pages, {})
    assert "polyhouse" in audit.attempted and "harvester" not in llm.calls
    assert output.polyhouse_assumptions.area_sq_m.value == 4000
    plain = _GroupLLM({})
    await extract_financial_sections(plain, PAGES, {})
    assert "polyhouse" not in plain.calls


def test_polyhouse_figures_must_appear_on_their_page():
    from app.services.dpr_agentic.financial_extraction import verify_polyhouse_pages
    from app.services.dpr_agentic.financial_schema import PolyhouseAssumptions

    fin = ProjectFinancials(
        polyhouse_assumptions=PolyhouseAssumptions(
            area_sq_m=ev(4000, page=1),
            structure_cost_lakh=ev(36, page=2),
            price_per_unit_rupees=ev(9, page=1),
        )
    )
    pages = [
        SimpleNamespace(page=1, text="Area in sq.mtrs 4,000.00; price per bunch 6"),
        SimpleNamespace(page=2, text="Polyhouse structure 36,00,000"),
    ]
    ph = verify_polyhouse_pages(fin, pages).polyhouse_assumptions
    assert ph.area_sq_m.value == 4000
    assert ph.structure_cost_lakh.value == 36  # printed in rupees
    assert ph.price_per_unit_rupees.status == "missing"


@pytest.mark.asyncio
async def test_dairy_and_cold_storage_groups_run_only_for_their_sector():
    from app.services.dpr_agentic.financial_schema import DairyAssumptions

    llm = _GroupLLM(
        {
            "dairy": ProjectFinancials(
                dairy_assumptions=DairyAssumptions(animals=ev(10, 4))
            )
        }
    )
    pages = PAGES[:3] + [
        SimpleNamespace(page=4, text="Technical: dairy unit of 10 milch animals")
    ]
    output, audit = await extract_financial_sections(llm, pages, {})
    assert "dairy" in audit.attempted and "cold_storage" not in llm.calls
    assert output.dairy_assumptions.animals.value == 10
    cold = _GroupLLM({})
    cold_pages = PAGES[:3] + [
        SimpleNamespace(page=4, text="Technical: cold storage of 5000 MT")
    ]
    await extract_financial_sections(cold, cold_pages, {})
    assert "cold_storage" in cold.calls and "dairy" not in cold.calls
