"""The bank ratios of the CAM's recomputed-figures page, computed in code
from the DPR's own projections (ProjectFinancials). No LLM, no I/O.

Each is a CalculationResult whose stated_value is the DPR's own printed
ratio (ProjectFinancials.stated_ratios, copied as printed) and whose
`match` says only whether the two agree. They are measures, not checks:
financial_calc.MEASURE_CHECKS keeps them out of the findings and out of
the readiness score, because a DPR's ratio often differs from ours by
method (a terminal value in the IRR, a different break-even basis), not
by error. The bank baselines are applied where the report is built
(cam_review.financial_review), which knows the scheme.

Definitions (amounts in lakh):
- project_irr: the rate at which -project cost (year 0) plus each year's
  net profit + depreciation + interest on term loan sums to zero. No
  terminal or salvage value is added, so it is conservative.
- payback_years: years until the cumulative cash accruals (net profit +
  depreciation) recover the project cost, interpolated within the year.
- debt_equity: term loan / (project cost - term loan); everything that
  is not the loan (own share, subsidy) counts as equity.
- fixed_asset_coverage: the lowest (fixed assets - depreciation so far) /
  (loan - principal repaid so far), from the start to the last repayment
  year; fixed assets are the project cost without working capital and
  preliminary / pre-operative expenses.
- cash_break_even: cash fixed costs / contribution at full capacity, in
  % of capacity, for the year of highest utilisation; the variable cost
  per unit (unit economics), or when none is printed, the variable share
  derived from the lowest- and highest-revenue years (high-low).
- dscr_average_price_10: the average DSCR with the selling price 10% lower
  and every cost unchanged (the stress box).
"""

from __future__ import annotations

import re

from .financial_calc import CalculationResult, _debt_service_for_year, dscr_parts
from .financial_schema import ExtractedValue, ProjectFinancials

RUPEES_PER_LAKH = 100_000
# A DPR's ratio this close to ours is the same figure (rounding).
IRR_TOLERANCE_PCT = 1.0
RATIO_TOLERANCE = 0.05
PAYBACK_TOLERANCE_YEARS = 0.5
BREAK_EVEN_TOLERANCE_PCT = 2.0
# The high-low split of cash_break_even needs revenue to change by at
# least this share between the two years it compares.
HIGH_LOW_MIN_REVENUE_SPREAD = 0.10
# IRR needs at least this many projection years to mean anything.
MIN_IRR_YEARS = 3
STRESS_PRICE_FALL_PCT = 10
# The stressed average DSCR the bank expects (the design's stress box).
STRESS_DSCR_FLOOR = 1.10

# Cost lines that are not fixed assets.
_NOT_FIXED_ASSET = re.compile(
    r"working\s*capital|w\.?\s*c\.?\s*margin|preliminary|pre[\s\-]*operative",
    re.I,
)
_LOAN_LINE = re.compile(r"loan|bank\s+finance", re.I)
_NOT_LOAN = re.compile(r"subsid|grant|promoter|own|margin", re.I)


def _ev(value: ExtractedValue | None) -> float | None:
    if value is None or value.status != "extracted":
        return None
    return value.value


def project_cost(fin: ProjectFinancials) -> float | None:
    if fin.cost_of_project_stated_total:
        return fin.cost_of_project_stated_total
    total = sum(c.amount for c in fin.cost_of_project_components)
    return round(total, 2) if total else None


def term_loan(fin: ProjectFinancials) -> float | None:
    if (loan := _ev(fin.term_loan_amount)) is not None:
        return loan
    lines = [
        c.amount
        for c in fin.means_of_finance_components
        if _LOAN_LINE.search(c.label) and not _NOT_LOAN.search(c.label)
    ]
    return round(sum(lines), 2) if lines else None


def fixed_assets(fin: ProjectFinancials) -> float | None:
    lines = [
        c.amount
        for c in fin.cost_of_project_components
        if not _NOT_FIXED_ASSET.search(c.label)
    ]
    return round(sum(lines), 2) if lines else None


def _stated(fin: ProjectFinancials, key: str) -> float | None:
    return _ev(getattr(fin.stated_ratios, key)) if fin.stated_ratios else None


def _result(check, computed, stated, tolerance, note=None) -> CalculationResult:
    if computed is None or stated is None:
        return CalculationResult(check, computed, stated, True, None, note)
    gap = round(computed - stated, 2)
    return CalculationResult(check, computed, stated, abs(gap) <= tolerance, gap, note)


def _years_from_one(fin: ProjectFinancials, *fields: str) -> list:
    """Projection years 1, 2, 3... while each has every field; stops at the
    first gap, so a sum never skips a year."""
    by_year = {y.year: y for y in fin.years}
    out = []
    year = 1
    while (y := by_year.get(year)) is not None and all(
        getattr(y, f) is not None for f in fields
    ):
        out.append(y)
        year += 1
    return out


def _interest(fin: ProjectFinancials, y) -> float:
    if y.interest_on_term_loan is not None:
        return y.interest_on_term_loan
    _, interest = _debt_service_for_year(fin, y)
    return interest or 0.0


def _npv(rate: float, flows: list[float]) -> float:
    return sum(f / (1 + rate) ** t for t, f in enumerate(flows))


def _irr(flows: list[float]) -> float | None:
    low, high = -0.99, 10.0
    if _npv(low, flows) * _npv(high, flows) > 0:
        return None
    for _ in range(200):
        mid = (low + high) / 2
        if _npv(low, flows) * _npv(mid, flows) <= 0:
            high = mid
        else:
            low = mid
    return (low + high) / 2


def project_irr(fin: ProjectFinancials) -> CalculationResult | None:
    cost = project_cost(fin)
    years = _years_from_one(fin, "net_profit", "depreciation")
    if not cost or len(years) < MIN_IRR_YEARS:
        return None
    flows = [-cost] + [y.net_profit + y.depreciation + _interest(fin, y) for y in years]
    irr = _irr(flows)
    if irr is None:
        return None
    return _result(
        "project_irr",
        round(irr * 100, 2),
        _stated(fin, "irr_pct"),
        IRR_TOLERANCE_PCT,
        note=f"{len(years)} years, no terminal value",
    )


def payback_years(fin: ProjectFinancials) -> CalculationResult | None:
    cost = project_cost(fin)
    years = _years_from_one(fin, "net_profit", "depreciation")
    if not cost or not years:
        return None
    stated = _stated(fin, "payback_years")
    recovered = 0.0
    for y in years:
        accrual = y.net_profit + y.depreciation
        if accrual > 0 and recovered + accrual >= cost:
            value = round(y.year - 1 + (cost - recovered) / accrual, 1)
            return _result("payback_years", value, stated, PAYBACK_TOLERANCE_YEARS)
        recovered += accrual
    # Not recovered within the projection: a DPR claiming it is is wrong.
    horizon = len(years)
    return CalculationResult(
        "payback_years",
        None,
        stated,
        stated is None or stated > horizon,
        None,
        note=f"beyond {horizon} years",
    )


def debt_equity(fin: ProjectFinancials) -> CalculationResult | None:
    cost, loan = project_cost(fin), term_loan(fin)
    if not cost or not loan or loan >= cost:
        return None
    return _result(
        "debt_equity",
        round(loan / (cost - loan), 2),
        _stated(fin, "debt_equity"),
        RATIO_TOLERANCE,
    )


def fixed_asset_coverage(fin: ProjectFinancials) -> CalculationResult | None:
    assets, loan = fixed_assets(fin), term_loan(fin)
    if not assets or not loan:
        return None
    lowest = (round(assets / loan, 2), "at start")
    net_assets, outstanding = assets, loan
    for y in _years_from_one(fin, "depreciation"):
        principal, interest = _debt_service_for_year(fin, y)
        if principal is None and y.total_instalment is not None:
            paid_interest = y.interest_on_term_loan or interest
            if paid_interest is None:
                break
            principal = y.total_instalment - paid_interest
        if principal is None:
            break
        net_assets -= y.depreciation
        outstanding -= principal
        if outstanding <= 0.01:
            break
        cover = round(net_assets / outstanding, 2)
        if cover < lowest[0]:
            lowest = (cover, f"year {y.year}")
    return _result(
        "fixed_asset_coverage",
        lowest[0],
        _stated(fin, "fixed_asset_coverage"),
        RATIO_TOLERANCE,
        note=lowest[1],
    )


def _cash_costs(fin: ProjectFinancials, y) -> float | None:
    """A year's cash operating costs: revenue less profit before tax,
    depreciation and term-loan interest. None when a figure is missing."""
    if y.revenue is None or y.depreciation is None:
        return None
    if y.profit_before_tax is not None:
        pbt = y.profit_before_tax
    elif y.net_profit is not None:
        # No PBT line: tax (if any) counts as a cost, which can only
        # raise the break-even -- the cautious side.
        pbt = y.net_profit + (y.tax_paid or 0.0)
    else:
        return None
    return y.revenue - pbt - y.depreciation - _interest(fin, y)


def _variable_share_from_years(rows) -> tuple[float, str] | None:
    """The variable cost per rupee of revenue when the DPR prints no
    variable cost per unit: how cash costs move between the lowest- and
    highest-revenue years (high-low method). Price or cost escalation in
    between blurs it, so a result outside (0, 1) or from too small a
    revenue change is not trusted."""
    if len(rows) < 2:
        return None
    low = min(rows, key=lambda r: r[1])
    high = max(rows, key=lambda r: r[1])
    spread = high[1] - low[1]
    if spread < HIGH_LOW_MIN_REVENUE_SPREAD * high[1]:
        return None
    share = (high[2] - low[2]) / spread
    if not 0 < share < 1:
        return None
    return share, f"variable cost derived from years {low[0]} and {high[0]}"


def cash_break_even(fin: ProjectFinancials) -> CalculationResult | None:
    """Uses the DPR's variable cost per unit when it prints one, otherwise
    the share derived from how costs move across the projection years."""
    from .unit_economics import (
        _unit_economics_of,
        _utilisation_by_year,
        full_capacity_per_year,
    )

    ue = _unit_economics_of(fin)
    if ue is None:
        return None
    price = _ev(ue.price_per_unit_rupees)
    variable = _ev(ue.variable_cost_per_unit_rupees)
    capacity = full_capacity_per_year(ue, fin)
    utilisation = _utilisation_by_year(ue, capacity)
    rows = []  # (year, revenue, cash costs, utilisation %)
    for y in fin.years:
        cash_costs = _cash_costs(fin, y)
        if cash_costs is None:
            continue
        util = utilisation.get(y.year)
        if not util and capacity and price:
            # No year-wise volume printed: the volume the revenue implies.
            util = min(y.revenue * RUPEES_PER_LAKH / price / capacity * 100, 100.0)
        if not util:
            continue
        rows.append((y.year, y.revenue, cash_costs, util))
    if price and variable is not None and variable < price:
        variable_share, how = variable / price, None
    elif derived := _variable_share_from_years(rows):
        variable_share, how = derived
    else:
        return None
    candidates = []
    for year, revenue, cash_costs, util in rows:
        fixed_cash = cash_costs - variable_share * revenue
        contribution_full = revenue * (1 - variable_share) / (util / 100)
        if fixed_cash < 0 or contribution_full <= 0:
            continue
        candidates.append((util, -year, fixed_cash / contribution_full * 100))
    if not candidates:
        return None
    util, minus_year, pct = max(candidates)
    basis = fin.stated_ratios.break_even_basis if fin.stated_ratios else None
    note = f"year {-minus_year}"
    if how:
        note += f"; {how}"
    if basis:
        note += f"; DPR basis: {basis}"
    return _result(
        "cash_break_even",
        round(pct, 1),
        _stated(fin, "break_even_pct"),
        BREAK_EVEN_TOLERANCE_PCT,
        note=note,
    )


def dscr_average_price_fall(fin: ProjectFinancials) -> CalculationResult | None:
    parts = dscr_parts(fin)
    if len(parts) < 2 or any(p[3] is None for p in parts):
        return None
    cut = STRESS_PRICE_FALL_PCT / 100
    value = round(
        sum(cash - cut * revenue for _, cash, _, revenue in parts)
        / sum(debt for _, _, debt, _ in parts),
        2,
    )
    return CalculationResult(
        f"dscr_average_price_{STRESS_PRICE_FALL_PCT}",
        value,
        STRESS_DSCR_FLOOR,
        value >= STRESS_DSCR_FLOOR,
        round(value - STRESS_DSCR_FLOOR, 2),
        note=f"{len(parts)} repayment years",
    )


def headroom(fin: ProjectFinancials, floor: float = STRESS_DSCR_FLOOR) -> dict | None:
    """How far the selling price can fall, or the costs rise, before the
    average DSCR reaches `floor`, in percent (0 when it is already below).
    Costs are revenue less the cash available for debt service (cash costs
    and tax). The smaller headroom is the most sensitive assumption."""
    parts = dscr_parts(fin)
    if len(parts) < 2 or any(p[3] is None for p in parts):
        return None
    cash = sum(p[1] for p in parts)
    debt = sum(p[2] for p in parts)
    revenue = sum(p[3] for p in parts)
    spare = max(cash - floor * debt, 0.0)
    costs = revenue - cash
    out = {"price_fall_pct": round(spare / revenue * 100, 1) if revenue > 0 else None}
    out["cost_rise_pct"] = round(spare / costs * 100, 1) if costs > 0 else None
    return out


def run_ratio_checks(fin: ProjectFinancials) -> list[CalculationResult]:
    checks = (
        project_irr(fin),
        payback_years(fin),
        debt_equity(fin),
        fixed_asset_coverage(fin),
        cash_break_even(fin),
        dscr_average_price_fall(fin),
    )
    return [c for c in checks if c is not None]
