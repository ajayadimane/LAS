"""The fixed benchmark table (benchmarks.py): machine class read from the
DPR, the same benchmark every run, should-fix findings anchored against the
verifier, and Annexure D showing what was used."""

from types import SimpleNamespace

from app.services.dpr_agentic.benchmarks import (
    HARVESTER_BENCHMARKS,
    detect_harvester_machine,
    run_benchmark_checks,
)
from app.services.dpr_agentic.cam_adapter import build_snapshot_from_analysis
from app.services.dpr_agentic.financial_schema import (
    ExtractedValue,
    HarvesterAssumptions,
    ProjectFinancials,
    UnitEconomics,
    UnitEconomicsYear,
)
from app.services.dpr_agentic.finding_judge import apply_verdicts, assign_ids
from app.services.dpr_agentic.schemas import AnalysisResult, Verdict


def ev(value, page=7):
    return ExtractedValue(value=value, page=page, status="extracted")


def pages(*texts):
    return [SimpleNamespace(page=i, text=t) for i, t in enumerate(texts, start=1)]


def machine(*texts):
    return detect_harvester_machine(pages(*texts))


# Wording as printed in the evaluation DPRs (typos included).
def test_machine_class_is_read_from_the_dpr_wording():
    large = machine(
        "SELF PROPELLED COMBINE HARVESTER MODEL: KARTAR 4000 SV CUTTER BAR : 14. FT."
    )
    assert (large.machine_class, large.cutter_bar_ft) == ("self_propelled_large", 14)
    swath = machine("CUTTER BAR SWATH 4267 MM (14 FEET HI-TENSILE) 101-133 HP")
    assert (swath.machine_class, swath.cutter_bar_ft, swath.engine_hp) == (
        "self_propelled_large",
        14,
        133,
    )
    small = machine("KARTAR-3500 GAJJENDRA 7 FT CUTTER BAR | ASHOK LEYLAND ENGINE")
    assert small.machine_class == "self_propelled_small"
    typo = machine("HARVESTER MODEL DASMESH-9100 SELF PROPELIED COMBINE HORVESTER")
    assert typo.machine_class == "self_propelled_large"  # size unknown: lenient
    hp_only = machine("HARVESTER MODEL VISHAL 435 133 HP BS IV COMBINE HARVESTER")
    assert hp_only.machine_class == "self_propelled_large"
    assert machine("Tractor mounted combine harvester, 8 ft").machine_class == (
        "tractor_mounted"
    )
    assert machine("Combine harvester for custom hiring").machine_class is None


def test_competitors_called_tractor_mounted_do_not_change_the_class():
    found = machine(
        "Siradi belt: Low availability (Tractor-mounted units)",
        "AS-930 SELF-PROPELLED COMBINE, 14 FT cutter bar",
    )
    assert found.machine_class == "self_propelled_large"
    assert not found.tractor_mounted


LARGE_DPR = pages("SELF PROPELLED COMBINE HARVESTER, 14 FT cutter bar, 133 HP")


def harvester(**fields):
    return ProjectFinancials(harvester_assumptions=HarvesterAssumptions(**fields))


def test_the_same_machine_gets_the_same_benchmark_every_time():
    bench = HARVESTER_BENCHMARKS["self_propelled_large"]
    typical = bench.typical_acres_per_year  # 16 acres/day x 75 days
    assert typical == 1200
    _, within, _ = run_benchmark_checks(harvester(acres_per_year=ev(1200)), LARGE_DPR)
    assert within == []
    results, [finding], record = run_benchmark_checks(
        harvester(acres_per_year=ev(1600, page=2)), LARGE_DPR
    )
    assert finding.check_id == "BENCHMARK_ACREAGE"
    assert finding.priority == "should_fix" and finding.provenance == "calculated"
    assert finding.evidence_page == 2
    assert "about 1200 acres a year" in finding.explanation
    assert "approved 2026-11-13" in finding.explanation
    assert results[0].check == "benchmark_acres_per_year" and not results[0].match
    assert record["benchmark"] == "self_propelled_large"
    assert record["machine"] == "self-propelled, 14 ft cutter bar, 133 HP"


def test_a_small_machine_is_held_to_its_own_class():
    small = pages("Self-propelled combine, 7 FT CUTTER BAR")
    _, [finding], _ = run_benchmark_checks(harvester(acres_per_year=ev(1200)), small)
    assert "about 600 acres a year" in finding.explanation  # 8 x 75
    assert "which the plan exceeds too" in finding.explanation  # top 10 x 90


def test_an_own_figures_acreage_finding_replaces_the_benchmark_one():
    fin = harvester(acres_per_year=ev(1600))
    results, findings, _ = run_benchmark_checks(fin, LARGE_DPR, acreage_flagged=True)
    assert findings == [] and results[0].match is False


def test_acres_per_day_and_rate_beyond_the_class():
    fin = harvester(acres_per_day=ev(25), price_per_acre=ev(2600))
    _, findings, _ = run_benchmark_checks(fin, LARGE_DPR)
    assert {f.check_id for f in findings} == {
        "BENCHMARK_ACRES_PER_DAY",
        "BENCHMARK_RATE",
    }
    ok = harvester(acres_per_day=ev(20), price_per_acre=ev(2000))
    assert run_benchmark_checks(ok, LARGE_DPR)[1] == []


def test_acreage_from_unit_economics_when_the_harvester_group_has_none():
    fin = harvester(price_per_acre=ev(1800))
    fin.unit_economics = UnitEconomics(
        unit_name="acres", years=[UnitEconomicsYear(year=2, units_sold=1600, page=9)]
    )
    _, [finding], _ = run_benchmark_checks(fin, LARGE_DPR)
    assert finding.evidence_page == 9


def test_other_sectors_are_not_checked_against_the_harvester_table():
    fin = ProjectFinancials(unit_economics=UnitEconomics(unit_name="litre"))
    assert run_benchmark_checks(fin, LARGE_DPR) == ([], [], None)


def test_the_verifier_cannot_drop_or_downgrade_a_benchmark_finding():
    _, findings, _ = run_benchmark_checks(harvester(acres_per_year=ev(1600)), LARGE_DPR)
    [finding] = assign_ids(findings)
    for decision, severity in (("reject", None), ("downgrade", "low")):
        kept, rejected = apply_verdicts(
            [finding.model_copy()],
            [
                Verdict(
                    finding_id=finding.finding_id,
                    decision=decision,
                    reason="r",
                    new_severity=severity,
                )
            ],
        )
        assert rejected == []
        assert (kept[0].severity, kept[0].priority) == ("medium", "should_fix")


def test_annexure_d_names_the_machine_and_the_benchmark():
    fin = harvester(acres_per_year=ev(1600), price_per_acre=ev(1800))
    _, _, fin.benchmark_used = run_benchmark_checks(fin, LARGE_DPR)
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
    oa = snap.operational_assumptions.assumptions
    assert oa["Machine (read from the DPR)"].startswith("self-propelled, 14 ft")
    assert "12-20 acres/day, 60-90 operating days/year" in oa["Benchmark used"]
    assert "(approved 2026-11-13)" in oa["Benchmark used"]
    assert oa["Benchmark rate per acre"] == "1400-2200 rupees"
    assert oa["Benchmark basis"].startswith("Not from an official or bank dataset")


# --- Polyhouse ---------------------------------------------------------------

from app.services.dpr_agentic.financial_schema import (  # noqa: E402
    PolyhouseAssumptions,
    YearFinancials,
)


def polyhouse(revenue_1=None, **fields):
    defaults = dict(structure_type="NVPH", crop="Dutch Rose", area_sq_m=ev(4000, 19))
    defaults.update(fields)
    return ProjectFinancials(
        polyhouse_assumptions=PolyhouseAssumptions(**defaults),
        years=[YearFinancials(year=1, revenue=revenue_1)] if revenue_1 else [],
    )


def checks(fin, *texts):
    return run_benchmark_checks(fin, pages(*texts))


def test_a_polyhouse_within_the_norms_has_no_findings():
    """The approved polyhouse DPR in the evaluation set: 36 lakh for 4000 m2
    (900/m2), year-1 revenue 38.475 lakh at 6 rupees a bunch (160/m2)."""
    fin = polyhouse(
        revenue_1=38.475,
        produce_unit="Bunch",
        structure_cost_lakh=ev(36, 23),
        price_per_unit_rupees=ev(6, 30),
    )
    results, findings, record = checks(fin)
    assert findings == []
    assert {r.check: r.stated_value for r in results} == {
        "benchmark_structure_cost_per_sq_m": 900,
        "benchmark_yield_per_sq_m": 160.3,
        "benchmark_price_per_unit": 6,
    }
    oa = record["annexure"]
    assert oa["Structure (read from the DPR)"] == "NVPH 4000 m2 growing Dutch Rose"
    assert (
        "844-950 rupees/m2 (approved 2026-11-13)"
        in oa["Benchmark used (structure cost)"]
    )
    assert oa["Yield per m2 (computed)"].startswith("160.3 (implied")


def test_structure_cost_above_the_norm_is_should_fix():
    fin = polyhouse(structure_cost_lakh=ev(48, 23))  # 1200/m2
    _, [finding], _ = checks(fin)
    assert finding.check_id == "BENCHMARK_STRUCTURE_COST"
    assert finding.priority == "should_fix" and finding.evidence_page == 23
    assert "1,200 rupees per m2" in finding.explanation
    # An unstated structure type gets the widest official range (lenient).
    unknown = polyhouse(structure_type=None, structure_cost_lakh=ev(48, 23))
    assert checks(unknown, "a protected cultivation project")[1] == []
    # ... unless the pages name the type.
    assert len(checks(unknown, "Polyhouse (NVPH) of 4,000 sq.mt")[1]) == 1


def test_yield_from_per_plant_figures_above_the_benchmark():
    fin = polyhouse(yield_per_plant_per_year=ev(33, 21), plants=ev(32000, 21))  # 264/m2
    _, [finding], _ = checks(fin)
    assert finding.check_id == "BENCHMARK_YIELD"
    assert "264 stems per m2" in finding.explanation
    assert "33 per plant x 32000 plants" in finding.explanation


def test_price_above_the_range_and_prices_not_per_piece_are_skipped():
    _, [finding], _ = checks(polyhouse(price_per_unit_rupees=ev(12, 30)))
    assert finding.check_id == "BENCHMARK_RATE"
    per_kg = polyhouse(produce_unit="kg", price_per_unit_rupees=ev(12, 30))
    results, findings, _ = checks(per_kg)
    assert results == [] and findings == []


def test_a_crop_without_a_norm_only_gets_the_structure_check():
    fin = polyhouse(crop="Coloured capsicum", price_per_unit_rupees=ev(80, 30))
    results, findings, record = checks(fin)
    assert results == [] and findings == []
    assert "Benchmark used (crop)" not in record["annexure"]


def test_polyhouse_dprs_get_no_search_based_findings():
    from app.services.dpr_agentic.grounding import benchmark_findings
    from app.services.dpr_agentic.schemas import ExternalStatement

    fin = polyhouse()
    fin.unit_economics = UnitEconomics(unit_name="stem", price_per_unit_rupees=ev(20))
    rate = ExternalStatement(
        kind="research",
        text="price",
        metric="price_per_unit_rupees",
        value_low=2,
        value_high=4,
    )
    assert benchmark_findings([rate], fin) == []


# --- Dairy and cold storage ---------------------------------------------------

from app.services.dpr_agentic.financial_schema import (  # noqa: E402
    ColdStorageAssumptions,
    DairyAssumptions,
)


def dairy(**fields):
    return ProjectFinancials(dairy_assumptions=DairyAssumptions(**fields))


def test_dairy_breed_picks_the_yield_benchmark():
    cow = dairy(animal_type="HF crossbred cow", milk_yield_litres_per_day=ev(12))
    assert checks(cow)[1] == []  # top of 7-12
    buffalo = dairy(animal_type="Murrah buffalo", milk_yield_litres_per_day=ev(12))
    _, [finding], record = checks(buffalo)
    assert finding.check_id == "BENCHMARK_MILK_YIELD"
    assert finding.priority == "should_fix" and "6-10" in finding.explanation
    assert record["benchmark"] == "buffalo"
    # Breed from the pages when the extraction has none.
    gir = dairy(milk_yield_litres_per_day=ev(8))
    _, [finding], _ = checks(gir, "10 Gir cows, indigenous breed")
    assert "3-5" in finding.explanation
    # Unstated breed: the most lenient cow entry, said so in Annexure D.
    _, findings, record = checks(dairy(milk_yield_litres_per_day=ev(12)))
    assert findings == []
    assert "breed not stated" in record["annexure"]["Benchmark used (dairy)"]


def test_dairy_lactation_annual_milk_and_price():
    fin = dairy(
        animal_type="crossbred cow",
        animals=ev(10),
        lactation_days=ev(365),
        milk_per_year_litres=ev(45000),  # 4500 a cow > 12 x 305 = 3660
        milk_price_per_litre=ev(60),
    )
    _, findings, _ = checks(fin)
    assert {f.check_id for f in findings} == {
        "BENCHMARK_LACTATION",
        "BENCHMARK_MILK_PER_ANIMAL",
        "BENCHMARK_RATE",
    }
    ok = dairy(
        animal_type="crossbred cow",
        animals=ev(10),
        lactation_days=ev(270),
        milk_per_year_litres=ev(27000),
        milk_price_per_litre=ev(40),
    )
    assert checks(ok)[1] == []


def cold(**fields):
    defaults = dict(capacity_mt=ev(5000))
    defaults.update(fields)
    return ProjectFinancials(
        cold_storage_assumptions=ColdStorageAssumptions(**defaults)
    )


def test_cold_storage_cost_per_tonne_against_the_norm():
    within = cold(storage_type="multi-chamber PEB", capital_cost_lakh=ev(550))
    assert checks(within)[1] == []  # 11,000/MT within Type 2 10,000-12,000
    single = cold(storage_type="single chamber", capital_cost_lakh=ev(550))
    _, [finding], record = checks(single)
    assert finding.check_id == "BENCHMARK_STRUCTURE_COST"
    assert "11,000 rupees per tonne" in finding.explanation
    assert record["benchmark"] == "type_1"
    unstated = cold(capital_cost_lakh=ev(550))
    _, findings, record = checks(unstated, "a cold storage for potato")
    assert findings == []
    assert "type not stated" in record["annexure"]["Benchmark used (cold storage)"]


def test_cold_storage_capacity_and_potato_rent():
    fin = cold(
        storage_type="Type 1",
        commodity="Potato",
        chamber_volume_cubic_m=ev(10200),  # holds 3000 MT
        rent_per_quintal_rupees=ev(450),
        rent_basis="season",
    )
    _, findings, record = checks(fin)
    assert {f.check_id for f in findings} == {"BENCHMARK_CAPACITY", "BENCHMARK_RATE"}
    assert "potato rent 225-380" in record["annexure"]["Benchmark used (cold storage)"]
    # Rent per month, or for another commodity, is not compared.
    monthly = cold(
        commodity="Potato", rent_per_quintal_rupees=ev(450), rent_basis="month"
    )
    apples = cold(commodity="Apple", rent_per_quintal_rupees=ev(450))
    assert checks(monthly)[1] == [] and checks(apples)[1] == []


def test_a_dpr_in_two_sectors_gets_both_and_no_search_findings():
    from app.services.dpr_agentic.benchmarks import covered_by_table

    fin = dairy(animal_type="Murrah", milk_yield_litres_per_day=ev(12))
    fin.cold_storage_assumptions = ColdStorageAssumptions(
        capacity_mt=ev(100), capital_cost_lakh=ev(20)
    )
    _, findings, record = checks(fin)
    assert {f.check_id for f in findings} == {
        "BENCHMARK_MILK_YIELD",
        "BENCHMARK_STRUCTURE_COST",
    }
    assert record["sector"] == "dairy+cold_storage"
    assert "Benchmark used (dairy)" in record["annexure"]
    assert covered_by_table(fin) and not covered_by_table(ProjectFinancials())


def test_public_model_projects_as_extracted_live():
    """What the extraction read from NABARD's 10-buffalo model and Bihar's
    cold storage Type II model project (2026-11-13). NABARD's own 10 litres
    a day must not be flagged; the Bihar model's 23,400 rupees a tonne is
    above the subsidy norm, which is what the finding says."""
    nabard = dairy(
        animal_type="Graded Murrah Buffalo",
        animals=ev(10, 5),
        milk_yield_litres_per_day=ev(10, 5),
        lactation_days=ev(270, 5),
        milk_price_per_litre=ev(26, 5),
    )
    assert checks(nabard)[1] == []
    bihar = cold(
        storage_type="Cold Storage Type - II",
        capacity_mt=ev(2000, 3),
        capital_cost_lakh=ev(468, 6),
    )
    _, [finding], _ = checks(bihar)
    assert finding.title == "Cold storage cost above the subsidy cost norm"
    assert "23,400 rupees per tonne" in finding.explanation
    assert "about 200-240 lakh of this cost" in finding.explanation
