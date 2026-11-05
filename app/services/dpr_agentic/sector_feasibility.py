"""Sector-specific technical-feasibility checks (brief Section 8), as a
plugin registry so a new sector is an addition, not a rewrite of existing
checks. Each plugin returns a CalculationTrace -- COMPUTED with a feasible
range, or NOT_COMPUTABLE with a reason (never a guessed number for a sector
or inputs it wasn't given).

Only harvester is implemented. Dairy and polyhouse are registered stubs
that always return NOT_COMPUTABLE, per the brief's explicit instruction to
leave them that way rather than invent sector assumptions this pipeline has
no agent-extracted data for yet (see PROGRESS.md).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from .cam_financial_engine import CALCULATION_VERSION, not_computable_trace
from .cam_snapshot import CalculationTrace


class SectorFeasibilityCheck(ABC):
    sector: str

    @abstractmethod
    def assess(self, assumptions: dict[str, Decimal]) -> CalculationTrace: ...


class HarvesterFeasibilityCheck(SectorFeasibilityCheck):
    sector = "harvester"

    def assess(self, assumptions: dict[str, Decimal]) -> CalculationTrace:
        required = ("acres_per_day", "operating_days_per_season", "seasons_per_year")
        missing = [k for k in required if k not in assumptions]
        inputs = {k: str(v) for k, v in assumptions.items()}
        formula = "Acres/day x operating days/season x seasons/year"
        if missing:
            return not_computable_trace(
                formula, inputs, f"Missing assumption(s): {', '.join(missing)}."
            )
        feasible_acres_per_year = (
            assumptions["acres_per_day"]
            * assumptions["operating_days_per_season"]
            * assumptions["seasons_per_year"]
        )
        return CalculationTrace(
            formula=formula,
            inputs=inputs,
            computed_value=feasible_acres_per_year,
            status="COMPUTED",
            calculation_version=CALCULATION_VERSION,
        )


class DairyFeasibilityCheck(SectorFeasibilityCheck):
    sector = "dairy"

    def assess(self, assumptions: dict[str, Decimal]) -> CalculationTrace:
        return not_computable_trace(
            "n/a",
            {k: str(v) for k, v in assumptions.items()},
            "Dairy sector feasibility checks (procurement capacity, yield, "
            "feed/input cost, spoilage, collection cycle) are not yet "
            "implemented.",
        )


class PolyhouseFeasibilityCheck(SectorFeasibilityCheck):
    sector = "polyhouse"

    def assess(self, assumptions: dict[str, Decimal]) -> CalculationTrace:
        return not_computable_trace(
            "n/a",
            {k: str(v) for k, v in assumptions.items()},
            "Polyhouse sector feasibility checks (cultivation area, "
            "production cycles, crop yield, price assumptions, water "
            "availability) are not yet implemented.",
        )


SECTOR_FEASIBILITY_REGISTRY: dict[str, SectorFeasibilityCheck] = {
    check.sector: check
    for check in (
        HarvesterFeasibilityCheck(),
        DairyFeasibilityCheck(),
        PolyhouseFeasibilityCheck(),
    )
}


def assess_sector_feasibility(
    sector: str, assumptions: dict[str, Decimal]
) -> CalculationTrace:
    check = SECTOR_FEASIBILITY_REGISTRY.get(sector)
    if check is None:
        return not_computable_trace(
            "n/a",
            {k: str(v) for k, v in assumptions.items()},
            f"No feasibility plugin registered for sector '{sector}'.",
        )
    return check.assess(assumptions)
