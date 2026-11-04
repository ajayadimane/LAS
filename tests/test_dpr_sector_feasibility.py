"""Milestone 2, Section 8: sector-specific technical-feasibility plugin
registry. Only harvester is implemented; dairy/polyhouse are stubs."""

from decimal import Decimal

from app.services.dpr_agentic.sector_feasibility import assess_sector_feasibility


def test_harvester_feasible_capacity_from_acres_per_day_and_season():
    """Tikamgarh: 10 acres/day x 30 days/season x 2 seasons (soybean Oct,
    wheat Apr) = 600 acres/year feasible -- the DPR claims 1,200."""
    trace = assess_sector_feasibility(
        "harvester",
        {
            "acres_per_day": Decimal("10"),
            "operating_days_per_season": Decimal("30"),
            "seasons_per_year": Decimal("2"),
        },
    )
    assert trace.status == "COMPUTED"
    assert trace.computed_value == Decimal("600")


def test_harvester_single_season_lower_bound():
    trace = assess_sector_feasibility(
        "harvester",
        {
            "acres_per_day": Decimal("10"),
            "operating_days_per_season": Decimal("30"),
            "seasons_per_year": Decimal("1"),
        },
    )
    assert trace.computed_value == Decimal("300")


def test_harvester_missing_assumption_is_not_computable():
    trace = assess_sector_feasibility("harvester", {"acres_per_day": Decimal("10")})
    assert trace.status == "NOT_COMPUTABLE"
    assert "operating_days_per_season" in trace.reason_not_computable


def test_dairy_is_a_documented_stub():
    trace = assess_sector_feasibility("dairy", {})
    assert trace.status == "NOT_COMPUTABLE"
    assert "not yet implemented" in trace.reason_not_computable


def test_polyhouse_is_a_documented_stub():
    trace = assess_sector_feasibility("polyhouse", {})
    assert trace.status == "NOT_COMPUTABLE"
    assert "not yet implemented" in trace.reason_not_computable


def test_unregistered_sector_is_not_computable_not_a_crash():
    trace = assess_sector_feasibility("aquaculture", {})
    assert trace.status == "NOT_COMPUTABLE"
    assert "aquaculture" in trace.reason_not_computable
