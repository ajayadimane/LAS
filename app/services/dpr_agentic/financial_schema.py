"""Structured financial facts extracted from a DPR -- numbers only, never narrative.

Populated by a dedicated extraction call (see orchestrator.FINANCIAL_EXTRACTION_PROMPT
and GeminiAgentClient.extract) whose only job is to copy figures the document
already states into this shape. Every field is optional: None means the
document didn't state it, never an invented or estimated value.
financial_calc.py is the only thing that computes anything from these.
"""

from typing import Literal

from pydantic import BaseModel, Field


class ExtractedValue(BaseModel):
    """A single figure plus the page it came from and whether it was found
    at all. Section 4.1 extension (milestone 2b): cam_financial_engine.py's
    sector/loan/fund-flow checks need a page reference per input to satisfy
    the Section 13 page-citation check, which the plain `float | None`
    fields elsewhere in this module (added before the engine existed) don't
    carry. New fields added for the engine use this; the pre-existing
    cost/means-of-finance/interest-rate fields are left as plain floats --
    changing those would touch financial_calc.py and its existing tests for
    no benefit the engine actually needs yet.

    `status="missing"` (not an absent/None value) is how the extraction
    call affirmatively reports "the document does not state this" without
    guessing a number -- distinguishing "I looked and it isn't there" from
    "I didn't look".
    """

    value: float | None = None
    page: int | None = None
    status: Literal["extracted", "missing"] = "missing"


class CrewRole(BaseModel):
    """One line of a DPR's staffing plan (e.g. its Annexure II)."""

    role: str
    count: int = Field(ge=0)
    annual_wage_per_person: float | None = None
    page: int | None = None
    status: Literal["extracted", "missing"] = "missing"


class HarvesterAssumptions(BaseModel):
    """Section 8/9 harvester-specific assumptions the Finance/Sector agents'
    narrative output doesn't carry in structured form. Added for
    cam_financial_engine.py / sector_feasibility.py (milestone 2b) -- see
    PROGRESS.md for why each field here couldn't be derived from the
    existing extraction (Section 4.1)."""

    acres_per_year: ExtractedValue = Field(default_factory=ExtractedValue)
    acres_per_day: ExtractedValue = Field(default_factory=ExtractedValue)
    operating_days_per_season: ExtractedValue = Field(default_factory=ExtractedValue)
    seasons_per_year: ExtractedValue = Field(default_factory=ExtractedValue)
    price_per_acre: ExtractedValue = Field(default_factory=ExtractedValue)
    running_cost_per_acre_stated: ExtractedValue = Field(default_factory=ExtractedValue)
    running_cost_per_acre_corrected: ExtractedValue = Field(
        default_factory=ExtractedValue
    )
    fuel_litres_per_acre: ExtractedValue = Field(default_factory=ExtractedValue)
    fuel_price_per_litre: ExtractedValue = Field(default_factory=ExtractedValue)
    fuel_cost_stated: ExtractedValue = Field(default_factory=ExtractedValue)
    crew_structure: list[CrewRole] = Field(default_factory=list)
    crew_wage_total_p_and_l_stated: ExtractedValue = Field(
        default_factory=ExtractedValue
    )


class PolyhouseAssumptions(BaseModel):
    """A protected-cultivation (polyhouse / greenhouse / shade-net) DPR's
    operating figures, copied as printed (the "polyhouse" extraction group).
    benchmarks.py checks them against the cost norms and crop yields."""

    structure_type: str | None = None  # as printed, e.g. "NVPH", "fan and pad"
    crop: str | None = None  # as printed, e.g. "Dutch Rose"
    produce_unit: str | None = None  # what is sold: "stem", "bunch", "kg"
    area_sq_m: ExtractedValue = Field(default_factory=ExtractedValue)
    structure_cost_lakh: ExtractedValue = Field(default_factory=ExtractedValue)
    plants: ExtractedValue = Field(default_factory=ExtractedValue)
    yield_per_sq_m_per_year: ExtractedValue = Field(default_factory=ExtractedValue)
    yield_per_plant_per_year: ExtractedValue = Field(default_factory=ExtractedValue)
    units_per_year: ExtractedValue = Field(default_factory=ExtractedValue)
    price_per_unit_rupees: ExtractedValue = Field(default_factory=ExtractedValue)


class DairyAssumptions(BaseModel):
    """A dairy DPR's herd and milk figures, copied as printed (the "dairy"
    extraction group). benchmarks.py checks them by breed."""

    animal_type: str | None = None  # as printed: "HF crossbred cow", "Murrah"
    animals: ExtractedValue = Field(default_factory=ExtractedValue)
    milk_yield_litres_per_day: ExtractedValue = Field(default_factory=ExtractedValue)
    lactation_days: ExtractedValue = Field(default_factory=ExtractedValue)
    milk_per_year_litres: ExtractedValue = Field(default_factory=ExtractedValue)
    milk_price_per_litre: ExtractedValue = Field(default_factory=ExtractedValue)
    feed_cost_per_animal_per_day: ExtractedValue = Field(default_factory=ExtractedValue)


class ColdStorageAssumptions(BaseModel):
    """A cold-storage DPR's capacity, cost and rent, copied as printed (the
    "cold_storage" extraction group). benchmarks.py checks them against the
    MIDH/NHB norms and market rents."""

    storage_type: str | None = None  # as printed: "multi-chamber PEB"...
    commodity: str | None = None  # main commodity stored, e.g. "potato"
    rent_basis: str | None = None  # "season" or "month", as printed
    capacity_mt: ExtractedValue = Field(default_factory=ExtractedValue)
    capital_cost_lakh: ExtractedValue = Field(default_factory=ExtractedValue)
    chamber_volume_cubic_m: ExtractedValue = Field(default_factory=ExtractedValue)
    rent_per_quintal_rupees: ExtractedValue = Field(default_factory=ExtractedValue)


class StatedRatios(BaseModel):
    """The DPR's own summary ratios and key assumptions, copied as printed
    (the "stated_ratios" extraction group). Never computed by the model:
    financial_ratios.py recomputes each from the projections and the CAM
    prints both side by side. Percentages are numbers (15.5 for 15.5%);
    a ratio printed "2.33:1" is 2.33."""

    irr_pct: ExtractedValue = Field(default_factory=ExtractedValue)
    average_dscr: ExtractedValue = Field(default_factory=ExtractedValue)
    break_even_pct: ExtractedValue = Field(default_factory=ExtractedValue)
    break_even_basis: str | None = None  # as printed: "cash", "accounting"...
    debt_equity: ExtractedValue = Field(default_factory=ExtractedValue)
    fixed_asset_coverage: ExtractedValue = Field(default_factory=ExtractedValue)
    payback_years: ExtractedValue = Field(default_factory=ExtractedValue)
    price_escalation_pct: ExtractedValue = Field(default_factory=ExtractedValue)
    cost_escalation_pct: ExtractedValue = Field(default_factory=ExtractedValue)
    depreciation_method: str | None = None  # as printed: "SLM", "WDV"...
    depreciation_rate_pct: ExtractedValue = Field(default_factory=ExtractedValue)


class UnitEconomicsYear(BaseModel):
    """One projection year's volume, as printed: utilisation and/or units
    sold. Either may be absent; unit_economics.py derives the other from
    capacity when it can."""

    year: int = Field(ge=1)
    utilisation_pct: float | None = None
    units_sold: float | None = None
    page: int | None = None


class UnitEconomics(BaseModel):
    """Sector-neutral operating assumptions behind the revenue line -- what
    the project produces or services (unit_name: acre, litre, tonne, hour,
    bed-night...), how much it can, how much it plans to, at what price and
    variable cost. Copied as printed; unit_economics.py computes everything.
    Prices and per-unit costs are in RUPEES per unit (not lakh): that is how
    DPRs print them. Every figure must be in the same unit_name; one stated
    in another unit is left missing, never converted by the model."""

    unit_name: str | None = None
    capacity_per_day: ExtractedValue = Field(default_factory=ExtractedValue)
    operating_days_per_year: ExtractedValue = Field(default_factory=ExtractedValue)
    capacity_per_year: ExtractedValue = Field(default_factory=ExtractedValue)
    price_per_unit_rupees: ExtractedValue = Field(default_factory=ExtractedValue)
    variable_cost_per_unit_rupees: ExtractedValue = Field(
        default_factory=ExtractedValue
    )
    years: list[UnitEconomicsYear] = Field(default_factory=list)


class RepaymentTableRow(BaseModel):
    """One year of a DPR's own repayment table, with a page reference per
    cell -- distinct from `YearFinancials` (which has no page tracking and
    is already consumed by financial_calc.py/tested; left unchanged)."""

    year: int = Field(ge=1)
    principal_due: ExtractedValue = Field(default_factory=ExtractedValue)
    interest_due: ExtractedValue = Field(default_factory=ExtractedValue)


class RepaymentInstalment(BaseModel):
    """One row of a monthly / quarterly / half-yearly repayment schedule,
    transcribed exactly as printed -- amounts in the document's own unit
    (ProjectFinancials.repayment_amount_unit), never added up or converted
    by the model. aggregate_repayment_instalments() turns these into yearly
    RepaymentTableRow totals in code. Seen live: a DPR printing an 84-row
    monthly schedule in whole rupees made Gemini exhaust its whole output
    budget "thinking" when asked for yearly lakh totals instead."""

    loan_year: int = Field(ge=1)  # 1 = the schedule's first year ("Year - I")
    period: str | None = None  # the row's own label, e.g. "Jul-27"
    instalment: float | None = None  # amount actually paid in this period
    interest: float | None = None
    # Printed principal component; negative when unpaid interest is
    # capitalised onto the balance (a moratorium month).
    principal: float | None = None
    page: int | None = None


class CostComponent(BaseModel):
    label: str
    amount: float = Field(ge=0)


class MeansOfFinanceComponent(BaseModel):
    label: str
    amount: float = Field(ge=0)


class YearFinancials(BaseModel):
    """One projected year's cashflow figures, for a DSCR computation.

    Our own DSCR = (net_profit + depreciation + interest_on_term_loan) /
                   (principal_repayment + interest_repayment) -- the
    standard formula, adding interest back into the numerator. Real DPR
    preparers do not all use this convention: some compute DSCR as
    (net_profit + depreciation) / (principal + interest), without the
    add-back, which is a materially different (lower) number from the
    same inputs. `stated_dscr` is the document's own DSCR for this year,
    copied verbatim -- never computed -- precisely so financial_calc.py
    can compare the two instead of asserting ours as the only truth.
    """

    year: int = Field(ge=1)
    revenue: float | None = None
    operating_expenses: float | None = None
    net_profit: float | None = None
    depreciation: float | None = None
    interest_on_term_loan: float | None = None
    principal_repayment: float | None = None
    interest_repayment: float | None = None
    # The year's full instalment (principal + interest together), when the
    # DPR prints it as one row ("TL Installment", "EMI"). It is the whole
    # debt service: never principal. Seen live: copied as principal, it made
    # a correct stated DSCR (13.54 / 4.13 = 3.28) look overstated.
    total_instalment: float | None = None
    stated_dscr: float | None = None
    # Added for cam_financial_engine.derive_tax_rate() (milestone 2b,
    # Section 4.1): the tax rate must be derived from the DPR's own stated
    # tax and PBT, never assumed, and neither figure was extracted before
    # the engine needed them.
    profit_before_tax: float | None = None
    tax_paid: float | None = None
    # Balance-sheet totals, read by code from the printed rows (yearly_rows.py),
    # never by Gemini: financial_calc.balance_sheet_balances compares them.
    total_assets: float | None = None
    total_liabilities: float | None = None


class ProjectFinancials(BaseModel):
    cost_of_project_components: list[CostComponent] = Field(default_factory=list)
    cost_of_project_stated_total: float | None = None
    means_of_finance_components: list[MeansOfFinanceComponent] = Field(
        default_factory=list
    )
    means_of_finance_stated_total: float | None = None
    # AIF-style interest subvention: effective_interest_rate_pct should
    # equal standard_interest_rate_pct - subvention_rate_pct (see
    # financial_calc.py's aif_subvention_reconciles()). All three are
    # independently verbatim, never derived from one another here.
    standard_interest_rate_pct: float | None = None
    subvention_rate_pct: float | None = None
    effective_interest_rate_pct: float | None = None
    years: list[YearFinancials] = Field(default_factory=list)

    # --- Added for cam_financial_engine.py (milestone 2b, Section 4.1) ---
    # Loan terms: the amortization schedule needs the amount, rate, tenor
    # and moratorium together, and none were previously extracted as a set
    # (means_of_finance_components has no page reference and no
    # tenor/moratorium at all).
    term_loan_amount: ExtractedValue = Field(default_factory=ExtractedValue)
    loan_tenor_years: ExtractedValue = Field(default_factory=ExtractedValue)
    loan_moratorium_years: ExtractedValue = Field(default_factory=ExtractedValue)
    # Fund-flow (sources vs. applications of funds) is a distinct statement
    # from cost-of-project/means-of-finance -- see fund_flow_gap()'s
    # docstring -- and was not extracted at all before the engine needed it.
    fund_flow_sources_total: ExtractedValue = Field(default_factory=ExtractedValue)
    fund_flow_applications_total: ExtractedValue = Field(default_factory=ExtractedValue)
    harvester_assumptions: HarvesterAssumptions | None = None
    unit_economics: UnitEconomics | None = None
    polyhouse_assumptions: PolyhouseAssumptions | None = None
    dairy_assumptions: DairyAssumptions | None = None
    cold_storage_assumptions: ColdStorageAssumptions | None = None
    stated_ratios: StatedRatios | None = None
    # Set by code, never by Gemini (not in FINANCIAL_GROUP_SCHEMAS): the
    # machine read from the DPR and the benchmark-table entry it was checked
    # against (benchmarks.run_benchmark_checks), for Annexure D.
    benchmark_used: dict | None = None
    repayment_table: list[RepaymentTableRow] = Field(default_factory=list)
    # A schedule printed per instalment instead of per year (see
    # RepaymentInstalment), and the unit its amounts are printed in.
    repayment_instalments: list[RepaymentInstalment] = Field(default_factory=list)
    repayment_amount_unit: Literal["lakh", "rupee"] | None = None


RUPEES_PER_LAKH = 100_000
# A year number this large is a calendar year ("2026"), not a projection
# year ("Year 1").
_CALENDAR_YEAR_FLOOR = 1900


def normalize_year_numbers(financials: ProjectFinancials) -> ProjectFinancials:
    """Projection years must be numbered 1..N, the convention every
    consumer (DSCR, repayment merge, CAM snapshot) relies on. Seen live: one
    extraction call numbered a P&L by calendar year (2026..2033) while the
    repayment call used 1..8 for the same document, so nothing lined up.
    When every year in `years` looks like a calendar year, renumber them
    1..N in order. Mixed or already-small numbers are left untouched."""

    def calendar(row_list) -> bool:
        years = [y.year for y in row_list]
        return bool(years) and min(years) >= _CALENDAR_YEAR_FLOOR

    ue = financials.unit_economics
    if not calendar(financials.years) and not (ue is not None and calendar(ue.years)):
        return financials
    fixed = financials.model_copy(deep=True)
    rows = [fixed.years]
    if fixed.unit_economics is not None:
        rows.append(fixed.unit_economics.years)
    for row_list in rows:
        if not calendar(row_list):
            continue
        years = [y.year for y in row_list]
        order = {year: i for i, year in enumerate(sorted(set(years)), start=1)}
        for y in row_list:
            y.year = order[y.year]
    return fixed


def aggregate_repayment_instalments(financials: ProjectFinancials) -> ProjectFinancials:
    """Builds yearly repayment_table rows from transcribed instalment rows
    -- the arithmetic the model is never asked to do. Only fills years the
    document did not already state as yearly totals.

    Per loan year: principal_due = sum of positive principal components
    (negative ones are capitalised interest, not repayment); interest_due =
    instalments paid minus that principal when the instalment column was
    transcribed, otherwise the sum of interest on rows that repaid
    principal. Converted to lakh from the printed unit; with no unit
    recorded, nothing is aggregated rather than guessing one.
    """
    rows = financials.repayment_instalments
    unit = financials.repayment_amount_unit
    if not rows or unit is None:
        return financials
    divisor = RUPEES_PER_LAKH if unit == "rupee" else 1
    existing = {r.year for r in financials.repayment_table}
    by_year: dict[int, list[RepaymentInstalment]] = {}
    for row in rows:
        by_year.setdefault(row.loan_year, []).append(row)
    fixed = financials.model_copy(deep=True)
    for year, items in sorted(by_year.items()):
        if year in existing:
            continue
        paying = [r for r in items if (r.principal or 0) > 0]
        if not paying:
            # A moratorium year repays nothing: no row, so DSCR is not
            # computed against a zero debt service.
            continue
        principal = sum(r.principal for r in paying)
        if all(r.instalment is not None for r in paying):
            interest = sum(r.instalment for r in paying) - principal
        else:
            interest = sum(r.interest or 0 for r in paying)
        page = next((r.page for r in items if r.page is not None), None)
        fixed.repayment_table.append(
            RepaymentTableRow(
                year=year,
                principal_due=ExtractedValue(
                    value=round(principal / divisor, 2), page=page, status="extracted"
                ),
                interest_due=ExtractedValue(
                    value=round(interest / divisor, 2), page=page, status="extracted"
                ),
            )
        )
    fixed.repayment_table.sort(key=lambda r: r.year)
    return fixed


def financial_extraction_completeness(financials: ProjectFinancials) -> dict:
    """Semantic quality gate: valid empty JSON is NOT completed extraction.

    Missing sections are separately reportable; absent values are never
    filled from an LLM-authored narrative or substituted with zero.
    """
    has_cost = (
        bool(financials.cost_of_project_components)
        or financials.cost_of_project_stated_total is not None
    )
    has_funding = bool(financials.means_of_finance_components) or (
        financials.term_loan_amount.status == "extracted"
        and financials.term_loan_amount.value is not None
    )
    has_repayment = bool(financials.repayment_table) or any(
        y.principal_repayment is not None or y.interest_repayment is not None
        for y in financials.years
    )
    # The DSCR's numerator: without net profit and depreciation for some
    # year, the repayment-capacity assessment cannot be made.
    has_pnl = any(
        y.net_profit is not None and y.depreciation is not None
        for y in financials.years
    )
    missing = [
        name
        for name, present in (
            ("project_cost", has_cost),
            ("means_of_finance", has_funding),
            ("repayment_schedule", has_repayment),
            ("profit_and_loss", has_pnl),
        )
        if not present
    ]
    return {
        "has_financial_data": has_cost
        or has_funding
        or has_repayment
        or bool(financials.years),
        "missing_sections": missing,
    }


def merge_missing_financials(
    original: ProjectFinancials, recovered: ProjectFinancials
) -> ProjectFinancials:
    """Combine bounded recovery without overwriting an already observed figure.

    Conflicts are deliberately not auto-resolved: the source values remain
    unchanged so independent verification can flag discrepancies.
    """
    base = original.model_copy(deep=True)
    for key in (
        "cost_of_project_components",
        "means_of_finance_components",
        "repayment_table",
        "repayment_instalments",
        "years",
    ):
        if not getattr(base, key) and getattr(recovered, key):
            setattr(base, key, getattr(recovered, key))
    for key in (
        "cost_of_project_stated_total",
        "means_of_finance_stated_total",
        "standard_interest_rate_pct",
        "subvention_rate_pct",
        "effective_interest_rate_pct",
    ):
        if getattr(base, key) is None and getattr(recovered, key) is not None:
            setattr(base, key, getattr(recovered, key))
    for key in (
        "term_loan_amount",
        "loan_tenor_years",
        "loan_moratorium_years",
        "fund_flow_sources_total",
        "fund_flow_applications_total",
    ):
        old, new = getattr(base, key), getattr(recovered, key)
        if (
            old.status != "extracted"
            and new.status == "extracted"
            and new.value is not None
        ):
            setattr(base, key, new)
    if (
        base.repayment_amount_unit is None
        and recovered.repayment_amount_unit is not None
    ):
        base.repayment_amount_unit = recovered.repayment_amount_unit
    if (
        base.harvester_assumptions is None
        and recovered.harvester_assumptions is not None
    ):
        base.harvester_assumptions = recovered.harvester_assumptions
    if base.unit_economics is None and recovered.unit_economics is not None:
        base.unit_economics = recovered.unit_economics
    for key in (
        "polyhouse_assumptions",
        "dairy_assumptions",
        "cold_storage_assumptions",
        "stated_ratios",
    ):
        if getattr(base, key) is None and getattr(recovered, key) is not None:
            setattr(base, key, getattr(recovered, key))
    return base
