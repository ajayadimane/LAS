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


class RepaymentTableRow(BaseModel):
    """One year of a DPR's own repayment table, with a page reference per
    cell -- distinct from `YearFinancials` (which has no page tracking and
    is already consumed by financial_calc.py/tested; left unchanged)."""

    year: int = Field(ge=1)
    principal_due: ExtractedValue = Field(default_factory=ExtractedValue)
    interest_due: ExtractedValue = Field(default_factory=ExtractedValue)


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
    net_profit: float | None = None
    depreciation: float | None = None
    interest_on_term_loan: float | None = None
    principal_repayment: float | None = None
    interest_repayment: float | None = None
    stated_dscr: float | None = None
    # Added for cam_financial_engine.derive_tax_rate() (milestone 2b,
    # Section 4.1): the tax rate must be derived from the DPR's own stated
    # tax and PBT, never assumed, and neither figure was extracted before
    # the engine needed them.
    profit_before_tax: float | None = None
    tax_paid: float | None = None


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
    repayment_table: list[RepaymentTableRow] = Field(default_factory=list)
