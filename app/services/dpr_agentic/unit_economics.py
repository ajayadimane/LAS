"""Sector-neutral unit economics: the operating checks behind a DPR's
revenue line, computed in code from figures Gemini copied as printed
(ProjectFinancials.unit_economics, page-verified in financial_extraction).

Why code, not an agent: the same operating issues -- revenue without a
volume build-up, full capacity from year 1, a machine idle most of the
year -- came out of the agents under different titles and severities on
every run of the same DPR (see PROGRESS.md, 2026-11-12). A calculation
gives the same finding every time, and the verifier cannot drop it.

Checks (each a CalculationResult, so agents see it in calculated_facts,
and a Finding when it fires):

- units_exceed_capacity: a year's units sold above full capacity
  (capacity_per_year, else capacity_per_day x operating_days_per_year).
  Skipped for a harvester DPR whose acreage check already ran -- the same
  issue, computed from the same figures.
- year1_full_utilisation: year-1 utilisation >= YEAR1_FULL_UTILISATION_PCT
  (bank reference CAM template, must-fix #4 "Full capacity assumed from
  Year 1").
- revenue_rebuild: units x price vs the P&L's stated revenue, per year;
  one finding for every year that differs by more than REVENUE_TOLERANCE.
- negative_contribution: price per unit at or below variable cost per unit.
- operating_days_idle: operating days <= IDLE_DAYS_THRESHOLD (template
  should-fix #9 "Machine idle about 10 months").

Thresholds are deliberately few and named here so a credit reviewer can
change them; priorities come from finding_rules.priority_for_calculation.
Pure functions; no LLM, no I/O.
"""


from __future__ import annotations

from .financial_calc import CalculationResult
from .financial_schema import ExtractedValue, ProjectFinancials, UnitEconomics
from .finding_rules import compute_dedup_key, priority_for_calculation
from .schemas import Finding

# 1.1.0 (2026-11-25): revenue rebuilt with the DPR's price escalation; the
#   worst year, not the first, rates the revenue finding.
CALCULATION_VERSION = "1.1.0"

RUPEES_PER_LAKH = 100_000
# Year-1 utilisation at or above this is "full capacity from day one".
YEAR1_FULL_UTILISATION_PCT = 90.0
# Rebuilt revenue within this share of the stated figure is a match
# (rounding in printed prices / volumes).
REVENUE_TOLERANCE = 0.02
# Operating days at or below this leave the asset idle most of the year.
IDLE_DAYS_THRESHOLD = 90
# Units sold within this share above capacity is rounding, not a defect.
CAPACITY_TOLERANCE = 0.01


def _ev(value: ExtractedValue) -> float | None:
    return (
        value.value if value.status == "extracted" and value.value is not None else None
    )


def full_capacity_per_year(
    ue: UnitEconomics, fin: ProjectFinancials | None = None
) -> float | None:
    """The DPR's full annual volume: stated capacity, else capacity/day x
    days, else -- because the model files a DPR's single annual target
    under different fields from run to run (seen live: capacity in one run,
    units sold in another, nowhere in a third) -- the harvester group's
    acres per year, else the largest per-year volume printed."""
    stated = _ev(ue.capacity_per_year)
    if stated:
        return stated
    per_day, days = _ev(ue.capacity_per_day), _ev(ue.operating_days_per_year)
    if per_day and days:
        return round(per_day * days, 2)
    h = fin.harvester_assumptions if fin is not None else None
    if h is not None and (acres := _ev(h.acres_per_year)):
        return acres
    sold = [y.units_sold for y in ue.years if y.units_sold]
    return max(sold) if sold else None


def _unit_economics_of(fin: ProjectFinancials) -> UnitEconomics | None:
    """fin.unit_economics, or one built from the harvester group (rate and
    running cost per acre) when the generic group returned nothing."""
    if fin.unit_economics is not None:
        return fin.unit_economics
    h = fin.harvester_assumptions
    if h is None or _ev(h.price_per_acre) is None:
        return None
    return UnitEconomics(
        unit_name="acre",
        price_per_unit_rupees=h.price_per_acre,
        variable_cost_per_unit_rupees=h.running_cost_per_acre_stated,
    )


def _units_by_year(ue: UnitEconomics, capacity: float | None) -> dict[int, float]:
    out = {}
    for y in ue.years:
        if y.units_sold is not None:
            out[y.year] = y.units_sold
        elif y.utilisation_pct is not None and capacity:
            out[y.year] = round(capacity * y.utilisation_pct / 100, 2)
    return out


def _utilisation_by_year(ue: UnitEconomics, capacity: float | None) -> dict[int, float]:
    out = {}
    for y in ue.years:
        if y.utilisation_pct is not None:
            out[y.year] = y.utilisation_pct
        elif y.units_sold is not None and capacity:
            out[y.year] = round(y.units_sold / capacity * 100, 1)
    return out


def _price_escalation(fin) -> float:
    """The DPR's own yearly price escalation in percent, 0 when not printed."""
    ratios = getattr(fin, "stated_ratios", None)
    ev = getattr(ratios, "price_escalation_pct", None) if ratios else None
    if ev is None or ev.status != "extracted" or ev.value is None:
        return 0.0
    return float(ev.value)


def _page(ue: UnitEconomics, year: int | None = None) -> int | None:
    if year is not None:
        page = next((y.page for y in ue.years if y.year == year), None)
        if page is not None:
            return page
    for ev in (ue.capacity_per_year, ue.capacity_per_day, ue.price_per_unit_rupees):
        if ev.page is not None:
            return ev.page
    return None


def _finding(
    r: CalculationResult,
    *,
    title: str,
    explanation: str,
    action: str,
    page: int | None,
    category: str,
    severity: str | None = None,
    stated_display: str | None = None,
    computed_display: str | None = None,
) -> Finding:
    priority, rule = priority_for_calculation(
        check=r.check, computed_value=r.computed_value, stated_value=r.stated_value
    )
    finding = Finding(
        category=category,
        severity=severity or ("high" if priority == "must_fix" else "medium"),
        title=title,
        explanation=explanation,
        evidence_page=page,
        suggested_action=action,
        provenance="calculated",
        verification="source_verified",
    )
    finding.priority, finding.priority_rule = priority, rule
    finding.finding_class = "document_defect"
    finding.dedup_key = compute_dedup_key(finding)
    finding.dpr_stated_value = stated_display
    finding.recomputed_display_value = computed_display
    finding.why_it_matters = WHY_BY_CHECK.get(r.check.rsplit("_year_", 1)[0])
    return finding


# The action plan's "why it matters", per check.
WHY_BY_CHECK = {
    "units_exceed_capacity": "Revenue rests on more output than the asset can give",
    "year1_full_utilisation": "Banks expect a ramp-up, not full use from day one",
    "revenue_rebuild": "Revenue that does not follow volume x price is queried",
    "negative_contribution": "Each unit sold would lose money",
    "operating_days_idle": "An asset idle most of the year must still repay",
}


def run_unit_economics(
    fin: ProjectFinancials, *, acreage_checked: bool = False
) -> tuple[list[CalculationResult], list[Finding]]:
    """`acreage_checked`: the harvester engine already compared stated acres
    with machine throughput, so units_exceed_capacity would repeat it."""
    ue = _unit_economics_of(fin)
    if ue is None:
        return [], []
    unit = ue.unit_name or "unit"
    results: list[CalculationResult] = []
    findings: list[Finding] = []
    capacity = full_capacity_per_year(ue, fin)
    units = _units_by_year(ue, capacity)
    utilisation = _utilisation_by_year(ue, capacity)
    price = _ev(ue.price_per_unit_rupees)
    variable = _ev(ue.variable_cost_per_unit_rupees)
    # No printed volume for year 1: imply it from year-1 revenue / price.
    # Used only for utilisation (never for the revenue rebuild, which would
    # then compare revenue with itself). Seen live: a DPR printing 1200
    # acres, Rs 1800/acre and year-1 revenue 21.6 lakh -- full planned
    # volume from day one, the reference template's must-fix #4 -- with no
    # utilisation row, so the check never ran.
    implied_note = ""
    revenue_1 = next((y.revenue for y in fin.years if y.year == 1), None)
    if 1 not in utilisation and capacity and price and revenue_1:
        implied = revenue_1 * RUPEES_PER_LAKH / price
        utilisation[1] = round(implied / capacity * 100, 1)
        implied_note = (
            f" (implied: year-1 revenue {revenue_1:g} lakh at {price:g} "
            f"rupees per {unit} is {implied:g} {unit}s, against a full "
            f"capacity of {capacity:g})"
        )

    over: dict[int, float] = {}
    if capacity and not acreage_checked:
        over = {
            y: u
            for y, u in sorted(units.items())
            if u > capacity * (1 + CAPACITY_TOLERANCE)
        }
        if over:
            year, sold = next(iter(over.items()))
            r = CalculationResult(
                "units_exceed_capacity",
                capacity,
                sold,
                False,
                round(capacity - sold, 2),
            )
            results.append(r)
            listed = ", ".join(f"year {y}: {u:g}" for y, u in over.items())
            findings.append(
                _finding(
                    r,
                    category="operations",
                    title="Planned volume exceeds the stated full capacity",
                    explanation=(
                        f"Full capacity is {capacity:g} {unit}s a year from the "
                        f"document's own figures; it plans to sell more ({listed})."
                    ),
                    action=f"Reduce the planned {unit}s to what the capacity allows, "
                    "or show the additional capacity.",
                    page=_page(ue, year),
                    stated_display=f"{sold:g} {unit}s",
                    computed_display=f"{capacity:g} {unit}s",
                )
            )

    # A year 1 already over capacity is covered by that finding.
    if (u1 := utilisation.get(1)) is not None and 1 not in over:
        full = u1 >= YEAR1_FULL_UTILISATION_PCT
        r = CalculationResult("year1_full_utilisation", u1, None, not full, None)
        results.append(r)
        if full:
            findings.append(
                _finding(
                    r,
                    category="operations",
                    title="Full capacity assumed from year 1",
                    explanation=(
                        f"Year-1 capacity utilisation is {u1:g}%{implied_note}. "
                        "A new project "
                        "rarely runs at or near full capacity in its first year "
                        "(start-up, building a customer base, learning curve); "
                        "revenue and debt service in year 1 depend on it."
                    ),
                    action="Show a ramp-up (e.g. 60-70% in year 1, rising after) "
                    "and recompute revenue and DSCR on it.",
                    page=_page(ue, 1),
                    stated_display=f"{u1:g}% in year 1",
                    computed_display="About 60% in year 1, rising",
                )
            )

    if price is not None:
        stated = {y.year: y.revenue for y in fin.years if y.revenue is not None}
        escalation = _price_escalation(fin)
        gaps = []
        for year, sold in sorted(units.items()):
            if year not in stated or not stated[year]:
                continue
            # The printed price is year 1's; the DPR's own escalation
            # applies after that (6%/yr read as "revenue overstated" before).
            year_price = price * (1 + escalation / 100) ** (year - 1)
            rebuilt = round(sold * year_price / RUPEES_PER_LAKH, 2)
            matched = abs(rebuilt - stated[year]) <= REVENUE_TOLERANCE * abs(
                stated[year]
            )
            r = CalculationResult(
                f"revenue_rebuild_year_{year}",
                rebuilt,
                stated[year],
                matched,
                round(rebuilt - stated[year], 2),
            )
            results.append(r)
            if not matched:
                gaps.append(r)
        if gaps:
            # The worst year rates the finding, not merely the first one.
            first = max(
                gaps, key=lambda g: abs(g.discrepancy) / max(abs(g.stated_value), 1e-9)
            )
            summary = CalculationResult(
                "revenue_rebuild",
                first.computed_value,
                first.stated_value,
                False,
                first.discrepancy,
            )
            overstated = any(g.stated_value > g.computed_value for g in gaps)
            listed = "; ".join(
                f"year {g.check.rsplit('_', 1)[1]}: stated {g.stated_value:g}, "
                f"rebuilt {g.computed_value:g}"
                for g in gaps
            )
            findings.append(
                _finding(
                    summary,
                    category="revenue",
                    title="Revenue does not match volume x price",
                    explanation=(
                        f"Revenue rebuilt from the document's own volume and price "
                        f"({price:g} rupees per {unit}"
                        + (
                            f", rising {escalation:g}% a year as the DPR states"
                            if escalation
                            else ""
                        )
                        + f") differs from the revenue it states, in lakh ({listed})."
                        + (
                            " The stated revenue is higher than its own volume and "
                            "price support."
                            if overstated
                            else ""
                        )
                    ),
                    action="Make revenue equal volume x price in every year, or "
                    "explain the other income.",
                    page=_page(ue, int(first.check.rsplit("_", 1)[1])),
                    severity="high" if overstated else "medium",
                    stated_display=f"{first.stated_value:g} lakh",
                    computed_display=f"{first.computed_value:g} lakh",
                )
            )

    if price is not None and variable is not None:
        margin = round(price - variable, 2)
        r = CalculationResult("negative_contribution", margin, price, margin > 0, None)
        results.append(r)
        if margin <= 0:
            findings.append(
                _finding(
                    r,
                    category="operating_costs",
                    title="Price per unit does not cover the variable cost",
                    explanation=(
                        f"The document charges {price:g} rupees per {unit} but its "
                        f"variable cost is {variable:g} rupees per {unit}: every "
                        f"{unit} sold loses {abs(margin):g} rupees before fixed "
                        "costs and debt service."
                    ),
                    action="Correct the price or the per-unit cost.",
                    page=ue.variable_cost_per_unit_rupees.page or _page(ue),
                )
            )

    if (days := _ev(ue.operating_days_per_year)) is not None:
        idle = days <= IDLE_DAYS_THRESHOLD
        r = CalculationResult("operating_days_idle", days, None, not idle, None)
        results.append(r)
        if idle:
            idle_months = round((365 - days) / 30.4)
            findings.append(
                _finding(
                    r,
                    category="operations",
                    title="Asset idle most of the year",
                    explanation=(
                        f"The document plans {days:g} operating days a year, so the "
                        f"asset stands idle about {idle_months} months. Off-season "
                        "upkeep, storage and insurance still cost money, and all "
                        "income must come from a short window."
                    ),
                    action="Cost the off-season months, or show other uses for "
                    "the asset.",
                    page=ue.operating_days_per_year.page,
                    severity="medium",
                )
            )
    return results, findings


def operational_assumptions(fin: ProjectFinancials) -> dict[str, str]:
    """Figures and derived values for the CAM's operational-assumptions
    annexure (strings, as OperationalAssumptions.assumptions expects)."""
    ue = _unit_economics_of(fin)
    if ue is None:
        return {}
    unit = ue.unit_name or "unit"
    out: dict[str, str] = {}

    def put(label: str, ev: ExtractedValue, suffix: str = "") -> None:
        if (v := _ev(ev)) is not None:
            out[label] = (
                f"{v:g}{suffix} (page {ev.page})" if ev.page else f"{v:g}{suffix}"
            )

    out["Unit"] = unit
    put("Capacity per day", ue.capacity_per_day, f" {unit}s")
    put("Operating days per year", ue.operating_days_per_year)
    put("Full capacity per year", ue.capacity_per_year, f" {unit}s")
    if _ev(ue.capacity_per_year) is None and (cap := full_capacity_per_year(ue, fin)):
        out["Full capacity per year (computed)"] = f"{cap:g} {unit}s"
    put("Price per unit", ue.price_per_unit_rupees, f" rupees/{unit}")
    put("Variable cost per unit", ue.variable_cost_per_unit_rupees, f" rupees/{unit}")
    price, variable = (
        _ev(ue.price_per_unit_rupees),
        _ev(ue.variable_cost_per_unit_rupees),
    )
    if price and variable is not None:
        out["Contribution per unit (computed)"] = (
            f"{price - variable:g} rupees/{unit} "
            f"({(price - variable) / price:.0%} of price)"
        )
    for year, pct in sorted(
        _utilisation_by_year(ue, full_capacity_per_year(ue, fin)).items()
    ):
        out[f"Utilisation year {year}"] = f"{pct:g}%"
    return out
