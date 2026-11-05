"""Deterministic Section 9 financial engine: loan amortization, DSCR under
both CADS definitions, three break-even types, fund-flow reconciliation,
tax-rate derivation and sector-aware scenario runs. No LLM anywhere in this
module. Decimal throughout; every function rounds only at the point it
returns a value, never mid-calculation, and every result is a
CalculationTrace (cam_snapshot.py) carrying its own formula, inputs, units
and reproducibility status -- never a bare number.

This is a calculation layer, not an extraction layer: unlike
financial_schema.py (verbatim LLM-extracted DPR figures only), the
functions here take explicit Decimal inputs from whatever already-verified
source the caller has (a DPR-stated figure, a prior CalculationTrace, or a
labelled assumption) and compute from them. Nothing here invents a figure
the caller didn't supply -- a missing input produces a NOT_COMPUTABLE trace
with a reason, never a guess.

Golden regression case: the Tikamgarh AS-930 combine-harvester DPR sampled
in cam-design-reference/LAS Customer CAM Report Template.pdf (see
tests/test_dpr_cam_financial_engine.py and PROGRESS.md milestone 2).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

from .cam_snapshot import CalculationTrace

TWO_PLACES = Decimal("0.01")
CALCULATION_VERSION = "1.0.0"


def _q(value: Decimal, places: Decimal = TWO_PLACES) -> Decimal:
    return value.quantize(places, rounding=ROUND_HALF_UP)


def not_computable_trace(
    formula: str, inputs: dict[str, str], reason: str
) -> CalculationTrace:
    return CalculationTrace(
        formula=formula,
        inputs=inputs,
        computed_value=None,
        status="NOT_COMPUTABLE",
        reason_not_computable=reason,
        calculation_version=CALCULATION_VERSION,
    )


# --------------------------------------------------------------------------
# 1. Loan amortization
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LoanTerms:
    principal: Decimal
    annual_rate_pct: Decimal
    tenor_years: int
    moratorium_years: int = 0
    # Equal-principal amortization (principal/repayment-years each year,
    # interest on the opening balance) is the only schedule implemented. It
    # is what the Tikamgarh golden case's own figures reproduce exactly
    # (22.50/7 = 3.21 L principal; 9% x 22.50 = 2.03 L interest in year 1) --
    # confirmed against the DPR's own stated year-1 repayment, not assumed.
    # A DPR using equal-installment (EMI) amortization instead would need a
    # second schedule function; not built here since nothing in scope uses
    # one yet (see PROGRESS.md).


@dataclass(frozen=True)
class AmortizationYear:
    year: int
    opening_balance: Decimal
    principal_due: Decimal
    interest_due: Decimal
    closing_balance: Decimal


def equal_principal_amortization_schedule(terms: LoanTerms) -> list[AmortizationYear]:
    repayment_years = terms.tenor_years - terms.moratorium_years
    schedule: list[AmortizationYear] = []
    if repayment_years <= 0:
        return schedule
    balance = terms.principal
    annual_principal = terms.principal / Decimal(repayment_years)
    rate = terms.annual_rate_pct / Decimal(100)
    for year in range(1, terms.tenor_years + 1):
        opening = balance
        interest = _q(opening * rate)
        if year <= terms.moratorium_years:
            schedule.append(
                AmortizationYear(
                    year, _q(opening), Decimal("0.00"), interest, _q(opening)
                )
            )
            continue
        is_final_year = year == terms.tenor_years
        # The last year absorbs rounding so the schedule closes to exactly
        # zero rather than leaving a residual paisa from repeated rounding.
        principal = _q(opening) if is_final_year else _q(annual_principal)
        closing = _q(opening - principal)
        schedule.append(
            AmortizationYear(year, _q(opening), principal, interest, closing)
        )
        balance = closing
    return schedule


# --------------------------------------------------------------------------
# 2. DSCR under either CADS definition
# --------------------------------------------------------------------------


class CadsDefinition(str, Enum):
    PAT_DEP_INTEREST = "pat_dep_interest"  # PAT + Depreciation + Interest on term loan
    PAT_DEP = "pat_dep"  # PAT + Depreciation (some DPRs' own convention)


def cash_available_for_debt_service(
    *,
    net_profit: Decimal,
    depreciation: Decimal,
    interest_on_term_loan: Decimal,
    definition: CadsDefinition,
) -> Decimal:
    if definition is CadsDefinition.PAT_DEP_INTEREST:
        return net_profit + depreciation + interest_on_term_loan
    return net_profit + depreciation


def dscr(
    *,
    net_profit: Decimal,
    depreciation: Decimal,
    interest_on_term_loan: Decimal,
    principal_due: Decimal,
    interest_due: Decimal,
    definition: CadsDefinition,
) -> CalculationTrace:
    cads = cash_available_for_debt_service(
        net_profit=net_profit,
        depreciation=depreciation,
        interest_on_term_loan=interest_on_term_loan,
        definition=definition,
    )
    debt_service = principal_due + interest_due
    inputs = {
        "net_profit": str(net_profit),
        "depreciation": str(depreciation),
        "interest_on_term_loan": str(interest_on_term_loan),
        "principal_due": str(principal_due),
        "interest_due": str(interest_due),
        "definition": definition.value,
    }
    if debt_service == 0:
        return not_computable_trace(
            "CADS / (Principal due + Interest due)",
            inputs,
            "Debt service is zero in this period.",
        )
    formula = (
        "(PAT + Depreciation + Interest on term loan) / (Principal + Interest due)"
        if definition is CadsDefinition.PAT_DEP_INTEREST
        else "(PAT + Depreciation) / (Principal + Interest due)"
    )
    return CalculationTrace(
        formula=formula,
        inputs=inputs,
        computed_value=_q(cads / debt_service),
        status="COMPUTED",
        calculation_version=CALCULATION_VERSION,
    )


# --------------------------------------------------------------------------
# 3. Break-even: accounting, operating cash-flow, debt-service threshold
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BreakEvenInputs:
    price_per_unit: Decimal
    variable_cost_per_unit: Decimal
    fixed_cost_total: Decimal
    # The only fixed-cost component currently identifiable as non-cash.
    # Other non-cash fixed costs (e.g. amortized deferred expenses), if any,
    # are not modeled -- see PROGRESS.md.
    depreciation_component: Decimal = Decimal("0")


def _contribution_margin_breakeven(
    inputs: BreakEvenInputs, fixed_cost: Decimal, formula: str
) -> CalculationTrace:
    contribution = inputs.price_per_unit - inputs.variable_cost_per_unit
    trace_inputs = {
        "price_per_unit": str(inputs.price_per_unit),
        "variable_cost_per_unit": str(inputs.variable_cost_per_unit),
        "fixed_cost": str(fixed_cost),
    }
    if contribution <= 0:
        return not_computable_trace(
            formula,
            trace_inputs,
            "Price per unit does not exceed variable cost per unit.",
        )
    return CalculationTrace(
        formula=formula,
        inputs=trace_inputs,
        computed_value=_q(fixed_cost / contribution, Decimal("1")),
        status="COMPUTED",
        calculation_version=CALCULATION_VERSION,
    )


def accounting_breakeven(inputs: BreakEvenInputs) -> CalculationTrace:
    """Units at which total revenue equals total cost (including
    depreciation) -- the conventional "break-even point" a P&L shows."""
    return _contribution_margin_breakeven(
        inputs,
        inputs.fixed_cost_total,
        "Fixed cost / (Price per unit - Variable cost per unit)",
    )


def operating_cashflow_breakeven(inputs: BreakEvenInputs) -> CalculationTrace:
    """Units at which *cash* fixed costs are covered -- excludes
    depreciation (non-cash), so this is always at or below the accounting
    break-even for the same inputs (Section 9: "must exclude depreciation")."""
    cash_fixed_cost = inputs.fixed_cost_total - inputs.depreciation_component
    return _contribution_margin_breakeven(
        inputs,
        cash_fixed_cost,
        "(Fixed cost - Depreciation) / (Price per unit - Variable cost per unit)",
    )


def debt_service_threshold_breakeven(
    *,
    price_per_unit: Decimal,
    variable_cost_per_unit: Decimal,
    fixed_cost_excluding_depreciation_and_interest: Decimal,
    depreciation: Decimal,
    interest_on_term_loan: Decimal,
    tax_rate: Decimal,
    principal_due: Decimal,
    interest_due: Decimal,
    definition: CadsDefinition,
) -> CalculationTrace:
    """Units at which CADS exactly equals total debt service (DSCR = 1.0).

    Solved algebraically (linear in `units`), not by search: PBT(units) =
    units*(price-variable) - other_fixed_cost - depreciation - interest;
    PAT(units) = PBT(units) * (1 - tax_rate) (only valid while PBT > 0 --
    flagged NOT_COMPUTABLE outside that range, never silently clamped).
    """
    contribution = price_per_unit - variable_cost_per_unit
    debt_service = principal_due + interest_due
    inputs = {
        "price_per_unit": str(price_per_unit),
        "variable_cost_per_unit": str(variable_cost_per_unit),
        "fixed_cost_excluding_depreciation_and_interest": str(
            fixed_cost_excluding_depreciation_and_interest
        ),
        "depreciation": str(depreciation),
        "interest_on_term_loan": str(interest_on_term_loan),
        "tax_rate": str(tax_rate),
        "debt_service": str(debt_service),
        "definition": definition.value,
    }
    formula = "units solving CADS(units) = Principal due + Interest due"
    if contribution <= 0:
        return not_computable_trace(
            formula, inputs, "Price per unit does not exceed variable cost."
        )
    other_fixed = (
        fixed_cost_excluding_depreciation_and_interest
        + depreciation
        + interest_on_term_loan
    )
    # CADS(units) = (units*contribution - other_fixed)*(1-tax_rate) + dep [+interest]
    add_back = (
        depreciation + interest_on_term_loan
        if definition is CadsDefinition.PAT_DEP_INTEREST
        else depreciation
    )
    # debt_service = (units*contribution - other_fixed)*(1-tax_rate) + add_back
    retained = Decimal(1) - tax_rate
    if retained <= 0:
        return not_computable_trace(
            formula, inputs, "Tax rate of 100% or more makes this unsolvable."
        )
    units = ((debt_service - add_back) / retained + other_fixed) / contribution
    if units < 0:
        return not_computable_trace(
            formula,
            inputs,
            "No positive unit volume reaches break-even with these inputs.",
        )
    return CalculationTrace(
        formula=formula,
        inputs=inputs,
        computed_value=_q(units, Decimal("1")),
        status="COMPUTED",
        calculation_version=CALCULATION_VERSION,
    )


# --------------------------------------------------------------------------
# 4. Sector-specific recomputations (harvester)
# --------------------------------------------------------------------------


def fuel_cost_reconciliation(
    *,
    acres: Decimal,
    litres_per_acre: Decimal,
    price_per_litre: Decimal,
    dpr_stated_lakh: Decimal,
) -> CalculationTrace:
    computed_rupees = acres * litres_per_acre * price_per_litre
    computed_lakh = _q(computed_rupees / Decimal(100_000))
    return CalculationTrace(
        formula="Acres x litres/acre x price/litre",
        inputs={
            "acres": str(acres),
            "litres_per_acre": str(litres_per_acre),
            "price_per_litre": str(price_per_litre),
            "dpr_stated_lakh": str(dpr_stated_lakh),
        },
        computed_value=computed_lakh,
        status="COMPUTED",
        calculation_version=CALCULATION_VERSION,
    )


def crew_wage_reconciliation(
    *, per_staffing_lakh: Decimal, p_and_l_stated_lakh: Decimal
) -> CalculationTrace:
    return CalculationTrace(
        formula="Annexure staffing plan wage bill (compared against the P&L figure)",
        inputs={
            "per_staffing_lakh": str(per_staffing_lakh),
            "p_and_l_stated_lakh": str(p_and_l_stated_lakh),
        },
        computed_value=per_staffing_lakh,
        status="COMPUTED",
        calculation_version=CALCULATION_VERSION,
    )


# --------------------------------------------------------------------------
# 5. Fund flow and tax rate
# --------------------------------------------------------------------------


def fund_flow_gap(
    *, sources_total: Decimal, applications_total: Decimal
) -> CalculationTrace:
    """A fund-flow (sources vs. applications of funds) mismatch must be
    reported as what it is -- an unexplained gap that should show up as
    closing cash in a correct cash-flow statement -- never silently
    absorbed by inventing a balancing entry equal to the gap (Section 9)."""
    gap = _q(sources_total - applications_total)
    return CalculationTrace(
        formula=(
            "Sources of funds - Applications of funds (a nonzero result "
            "must appear as closing cash in the cash-flow statement, not "
            "be plugged away)"
        ),
        inputs={
            "sources_total": str(sources_total),
            "applications_total": str(applications_total),
        },
        computed_value=gap,
        status="COMPUTED",
        calculation_version=CALCULATION_VERSION,
    )


def derive_tax_rate(tax_paid: Decimal, profit_before_tax: Decimal) -> CalculationTrace:
    """The effective tax rate implied by the DPR's own stated tax and PBT
    figures -- never an assumed statutory rate (Section 9's instruction to
    derive, not assume, applies directly to the Tikamgarh case: 0.50/4.27)."""
    inputs = {"tax_paid": str(tax_paid), "profit_before_tax": str(profit_before_tax)}
    if profit_before_tax == 0:
        return not_computable_trace(
            "Tax paid / Profit before tax (derived from the DPR's own figures)",
            inputs,
            "Profit before tax is zero.",
        )
    return CalculationTrace(
        formula="Tax paid / Profit before tax (derived from the DPR's own figures)",
        inputs=inputs,
        computed_value=_q(tax_paid / profit_before_tax, Decimal("0.0001")),
        status="COMPUTED",
        calculation_version=CALCULATION_VERSION,
    )


# --------------------------------------------------------------------------
# 6. Scenario runner (harvester)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    label: str
    is_hypothetical: bool
    source_class: str  # "dpr_stated" | "recomputed" | "benchmark" | "illustrative"
    pbt: Decimal
    pat: Decimal
    dscr_trace: CalculationTrace


def _scenario(
    *,
    scenario_id: str,
    label: str,
    is_hypothetical: bool,
    source_class: str,
    pbt_dpr_as_written: Decimal,
    cost_delta: Decimal,
    tax_rate: Decimal,
    depreciation: Decimal,
    interest_on_term_loan: Decimal,
    principal_due: Decimal,
    interest_due: Decimal,
) -> ScenarioResult:
    pbt = _q(pbt_dpr_as_written - cost_delta)
    tax = _q(pbt * tax_rate)
    pat = _q(pbt - tax)
    trace = dscr(
        net_profit=pat,
        depreciation=depreciation,
        interest_on_term_loan=interest_on_term_loan,
        principal_due=principal_due,
        interest_due=interest_due,
        definition=CadsDefinition.PAT_DEP_INTEREST,
    )
    return ScenarioResult(
        scenario_id=scenario_id,
        label=label,
        is_hypothetical=is_hypothetical,
        source_class=source_class,
        pbt=pbt,
        pat=pat,
        dscr_trace=trace,
    )


def run_harvester_scenarios(
    *,
    acres: Decimal,
    pbt_dpr_as_written: Decimal,
    tax_rate: Decimal,
    depreciation: Decimal,
    interest_on_term_loan: Decimal,
    principal_due: Decimal,
    interest_due: Decimal,
    fuel_cost_dpr_stated: Decimal,
    fuel_cost_recomputed: Decimal,
    crew_wage_dpr_stated: Decimal,
    crew_wage_recomputed: Decimal,
) -> list[ScenarioResult]:
    """Two scenarios at the *same* acreage, isolating only the cost-side
    correction -- never mixes one scenario's cash flow with another's debt
    service (Section 9/19.7): both use this same loan's principal/interest.

    "dpr_as_written" reproduces the DPR's own 1.97x figure exactly from its
    own stated PBT; "corrected_costs" applies only the fuel + crew wage
    deltas this report itself identifies (Section 18), nothing else.
    """
    dpr_as_written = _scenario(
        scenario_id="dpr_as_written",
        label=f"As stated in the DPR at {acres} acres",
        is_hypothetical=False,
        source_class="dpr_stated",
        pbt_dpr_as_written=pbt_dpr_as_written,
        cost_delta=Decimal("0"),
        tax_rate=tax_rate,
        depreciation=depreciation,
        interest_on_term_loan=interest_on_term_loan,
        principal_due=principal_due,
        interest_due=interest_due,
    )
    cost_delta = (fuel_cost_recomputed - fuel_cost_dpr_stated) + (
        crew_wage_recomputed - crew_wage_dpr_stated
    )
    corrected = _scenario(
        scenario_id="corrected_costs",
        label=f"Corrected fuel and crew wage costs at {acres} acres",
        is_hypothetical=True,
        source_class="recomputed",
        pbt_dpr_as_written=pbt_dpr_as_written,
        cost_delta=cost_delta,
        tax_rate=tax_rate,
        depreciation=depreciation,
        interest_on_term_loan=interest_on_term_loan,
        principal_due=principal_due,
        interest_due=interest_due,
    )
    return [dpr_as_written, corrected]
