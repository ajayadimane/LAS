"""Bridges financial_schema.ProjectFinancials' milestone-2b extraction
fields into cam_financial_engine.py / sector_feasibility.py, behind
Settings.cam_engine_enabled (default off -- see orchestrator.py's call
site). Degrades per-figure, never all-or-nothing: whichever inputs are
present and marked "extracted" get computed; anything missing produces no
output for that piece rather than a guess. Never raises.

Produces two things:
  - `findings`: Finding objects for a reconciliation mismatch (fuel cost,
    crew wages, fund flow), merged into the existing calculated-findings
    pipeline exactly like financial_calc.findings_from_calculations().
  - `CamEngineOutputs`: a structured bundle of the richer CalculationTrace/
    scenario results, round-tripped through AnalysisResult.cam_engine_outputs
    (as a plain dict, so it survives report_store.py's JSON persistence)
    for cam_adapter.py to fold into CAMSnapshot's financial_ratios/
    repayment_schedule/scenario_analysis.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from .cam_financial_engine import (
    CALCULATION_VERSION,
    BreakEvenInputs,
    CadsDefinition,
    LoanTerms,
    accounting_breakeven,
    crew_wage_reconciliation,
    debt_service_threshold_breakeven,
    derive_tax_rate,
    dscr,
    equal_principal_amortization_schedule,
    fuel_cost_reconciliation,
    fund_flow_gap,
    operating_cashflow_breakeven,
    run_harvester_scenarios,
)
from .cam_snapshot import CalculationTrace
from .financial_calc import (
    CalculationResult,
    cost_of_project_matches_components,
    means_of_finance_balances_cost,
    means_of_finance_matches_components,
)
from .financial_schema import ExtractedValue, ProjectFinancials, YearFinancials
from .finding_rules import compute_dedup_key, priority_for_calculation
from .schemas import Finding
from .sector_feasibility import assess_sector_feasibility

MONEY_TOLERANCE = Decimal("0.01")  # lakh
MATERIAL_GAP_TOLERANCE = Decimal("1")  # lakh

# Keyword classification of a means-of-finance line's free-text label, as
# extracted verbatim from the DPR -- never a guess at an amount, only at
# which already-extracted line is the loan vs. the promoter's own
# contribution. A label matching none of these stays "other" rather than
# being forced into either bucket.
_LOAN_LABEL_KEYWORDS = ("loan", "bank", "sbi", "term loan")
_PROMOTER_LABEL_KEYWORDS = (
    "promoter",
    "own contribution",
    "own fund",
    "margin",
    "equity",
)


class ScenarioOutput(BaseModel):
    scenario_id: str
    label: str
    is_hypothetical: bool
    source_class: str
    dscr: CalculationTrace


class CostLineOutput(BaseModel):
    label: str
    amount: str  # Decimal as str, lakh


class FinanceLineOutput(BaseModel):
    label: str
    amount: str  # Decimal as str, lakh
    source_class: str  # "loan" | "promoter" | "other"


class CamEngineOutputs(BaseModel):
    dscr_pat_dep_interest: CalculationTrace | None = None
    dscr_pat_dep: CalculationTrace | None = None
    breakeven_accounting: CalculationTrace | None = None
    breakeven_operating_cashflow: CalculationTrace | None = None
    breakeven_debt_service_threshold: CalculationTrace | None = None
    feasible_acres_per_year: CalculationTrace | None = None
    fund_flow_gap: CalculationTrace | None = None
    tax_rate: CalculationTrace | None = None
    year1_principal_due: str | None = None
    year1_interest_due: str | None = None
    scenarios: list[ScenarioOutput] = Field(default_factory=list)

    # --- Cost of project / means of finance (closes the milestone-4 gap:
    # these were already extracted by financial_calc.py's own checks, just
    # never folded into CamEngineOutputs for cam_adapter.py to use) ---
    project_cost_components: list[CostLineOutput] = Field(default_factory=list)
    project_cost_stated_total: str | None = None
    cost_reconciliation: CalculationTrace | None = None
    means_of_finance_components: list[FinanceLineOutput] = Field(default_factory=list)
    means_of_finance_stated_total: str | None = None
    means_of_finance_reconciliation: CalculationTrace | None = None
    funding_balances_cost: CalculationTrace | None = None
    requested_loan: str | None = None
    promoter_contribution: str | None = None
    proposed_tenure_months: int | None = None


def _dec(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _extracted(ev: ExtractedValue) -> Decimal | None:
    if ev.status != "extracted" or ev.value is None:
        return None
    return Decimal(str(ev.value))


def _year(fin: ProjectFinancials, year: int) -> YearFinancials | None:
    return next((y for y in fin.years if y.year == year), None)


def _calculated_finding(
    *,
    check: str,
    title: str,
    explanation: str,
    page: int | None,
    computed_value: Decimal,
    stated_value: Decimal | None,
    unit: str = " lakh",
    show_value_pair: bool = True,
    category: str = "financial_calculation",
) -> Finding:
    priority, rule = priority_for_calculation(
        check=check,
        computed_value=float(computed_value),
        stated_value=float(stated_value) if stated_value is not None else None,
    )
    finding = Finding(
        category=category,
        severity="high" if priority == "must_fix" else "medium",
        title=title,
        explanation=explanation,
        evidence_page=page,
        suggested_action="Verify this figure with the applicant.",
        provenance="calculated",
        verification="source_verified",
    )
    finding.priority = priority
    finding.priority_rule = rule
    finding.finding_class = "document_defect"
    finding.dedup_key = compute_dedup_key(finding)
    if show_value_pair and stated_value is not None:
        finding.dpr_stated_value = f"{stated_value}{unit}"
        finding.recomputed_display_value = f"{computed_value}{unit}"
    return finding


def _loan_terms(fin: ProjectFinancials) -> LoanTerms | None:
    principal = _extracted(fin.term_loan_amount)
    tenor_value = _extracted(fin.loan_tenor_years)
    rate = _dec(fin.standard_interest_rate_pct)
    if principal is None or tenor_value is None or rate is None:
        return None
    moratorium_value = _extracted(fin.loan_moratorium_years)
    return LoanTerms(
        principal=principal,
        annual_rate_pct=rate,
        tenor_years=int(tenor_value),
        moratorium_years=int(moratorium_value) if moratorium_value is not None else 0,
    )


def _calc_result_to_trace(result: CalculationResult, formula: str) -> CalculationTrace:
    return CalculationTrace(
        formula=formula,
        inputs={
            "computed_value": str(result.computed_value),
            "stated_value": str(result.stated_value),
        },
        computed_value=_dec(result.discrepancy),
        status="COMPUTED",
        calculation_version=CALCULATION_VERSION,
    )


def _classify_finance_label(label: str) -> str:
    lowered = label.lower()
    if any(k in lowered for k in _LOAN_LABEL_KEYWORDS):
        return "loan"
    if any(k in lowered for k in _PROMOTER_LABEL_KEYWORDS):
        return "promoter"
    return "other"


def _cost_and_finance_fields(fin: ProjectFinancials, outputs: CamEngineOutputs) -> None:
    """Mutates `outputs` with project_cost/means_of_finance figures. These
    were already extracted by financial_schema.ProjectFinancials (and
    already used by financial_calc.py's own reconciliation checks) -- this
    is a mapping step, not a new extraction, so it runs unconditionally
    whenever the engine runs, independent of sector."""
    if fin.cost_of_project_components:
        outputs.project_cost_components = [
            CostLineOutput(label=c.label, amount=str(c.amount))
            for c in fin.cost_of_project_components
        ]
    if fin.cost_of_project_stated_total is not None:
        outputs.project_cost_stated_total = str(fin.cost_of_project_stated_total)
    if result := cost_of_project_matches_components(fin):
        outputs.cost_reconciliation = _calc_result_to_trace(
            result, "Sum of cost-of-project components vs. stated total"
        )

    if fin.means_of_finance_components:
        outputs.means_of_finance_components = [
            FinanceLineOutput(
                label=c.label,
                amount=str(c.amount),
                source_class=_classify_finance_label(c.label),
            )
            for c in fin.means_of_finance_components
        ]
        loan_line = next(
            (
                c
                for c in outputs.means_of_finance_components
                if c.source_class == "loan"
            ),
            None,
        )
        promoter_line = next(
            (
                c
                for c in outputs.means_of_finance_components
                if c.source_class == "promoter"
            ),
            None,
        )
        if loan_line:
            outputs.requested_loan = loan_line.amount
        if promoter_line:
            outputs.promoter_contribution = promoter_line.amount
    if fin.means_of_finance_stated_total is not None:
        outputs.means_of_finance_stated_total = str(fin.means_of_finance_stated_total)
    if result := means_of_finance_matches_components(fin):
        outputs.means_of_finance_reconciliation = _calc_result_to_trace(
            result, "Sum of means-of-finance components vs. stated total"
        )
    if result := means_of_finance_balances_cost(fin):
        outputs.funding_balances_cost = _calc_result_to_trace(
            result, "Means of finance total - cost of project total"
        )

    tenor_years = _extracted(fin.loan_tenor_years)
    if tenor_years is not None:
        outputs.proposed_tenure_months = int(tenor_years * 12)


def _fuel_finding(fin: ProjectFinancials) -> Finding | None:
    h = fin.harvester_assumptions
    if h is None:
        return None
    acres = _extracted(h.acres_per_year)
    litres = _extracted(h.fuel_litres_per_acre)
    price = _extracted(h.fuel_price_per_litre)
    stated = _extracted(h.fuel_cost_stated)
    if acres is None or litres is None or price is None or stated is None:
        return None
    trace = fuel_cost_reconciliation(
        acres=acres,
        litres_per_acre=litres,
        price_per_litre=price,
        dpr_stated_lakh=stated,
    )
    if (
        trace.computed_value is None
        or abs(trace.computed_value - stated) <= MONEY_TOLERANCE
    ):
        return None
    return _calculated_finding(
        check="fuel_cost_reconciliation",
        title="Fuel cost does not match acres x litres/acre x price",
        explanation=(
            f"{trace.formula} = {trace.computed_value} lakh; the document "
            f"states {stated} lakh."
        ),
        page=h.fuel_cost_stated.page or h.fuel_litres_per_acre.page,
        computed_value=trace.computed_value,
        stated_value=stated,
    )


def _crew_wage_finding(fin: ProjectFinancials) -> Finding | None:
    h = fin.harvester_assumptions
    if h is None or not h.crew_structure:
        return None
    stated = _extracted(h.crew_wage_total_p_and_l_stated)
    if stated is None:
        return None
    roles_extracted = [r for r in h.crew_structure if r.status == "extracted"]
    if not roles_extracted:
        return None
    per_staffing = sum(
        (
            Decimal(str(r.annual_wage_per_person)) * r.count
            for r in roles_extracted
            if r.annual_wage_per_person is not None
        ),
        start=Decimal("0"),
    )
    if per_staffing == 0:
        return None
    trace = crew_wage_reconciliation(
        per_staffing_lakh=per_staffing, p_and_l_stated_lakh=stated
    )
    if abs(trace.computed_value - stated) <= MONEY_TOLERANCE:
        return None
    return _calculated_finding(
        check="crew_wage_reconciliation",
        title="Crew wages understated versus the staffing plan",
        explanation=(
            f"Annexure staffing plan totals {per_staffing} lakh; the P&L "
            f"states {stated} lakh."
        ),
        page=h.crew_wage_total_p_and_l_stated.page,
        computed_value=per_staffing,
        stated_value=stated,
    )


def _fund_flow_finding(
    fin: ProjectFinancials,
) -> tuple[Finding | None, CalculationTrace | None]:
    sources = _extracted(fin.fund_flow_sources_total)
    applications = _extracted(fin.fund_flow_applications_total)
    if sources is None or applications is None:
        return None, None
    trace = fund_flow_gap(sources_total=sources, applications_total=applications)
    if (
        trace.computed_value is None
        or abs(trace.computed_value) <= MATERIAL_GAP_TOLERANCE
    ):
        return None, trace
    finding = _calculated_finding(
        check="fund_flow_sources_applications_balance",
        title="Fund flow sources do not match applications",
        explanation=(
            f"{trace.formula} Sources {sources} lakh, applications "
            f"{applications} lakh, gap {trace.computed_value} lakh."
        ),
        page=fin.fund_flow_sources_total.page,
        computed_value=sources,
        stated_value=applications,
        show_value_pair=False,
    )
    return finding, trace


def run_harvester_engine(
    fin: ProjectFinancials,
) -> tuple[CamEngineOutputs, list[Finding]]:
    """Returns (structured engine outputs, findings for mismatches found).
    Every piece degrades independently -- missing inputs for one
    calculation never block another."""
    outputs = CamEngineOutputs()
    findings: list[Finding] = []

    _cost_and_finance_fields(fin, outputs)

    terms = _loan_terms(fin)
    schedule = equal_principal_amortization_schedule(terms) if terms else []
    year1_amort = schedule[0] if schedule else None
    if year1_amort:
        outputs.year1_principal_due = str(year1_amort.principal_due)
        outputs.year1_interest_due = str(year1_amort.interest_due)

    year1 = _year(fin, 1)
    net_profit = _dec(year1.net_profit) if year1 else None
    depreciation = _dec(year1.depreciation) if year1 else None
    interest_on_loan = (
        _dec(year1.interest_on_term_loan)
        if year1 and year1.interest_on_term_loan is not None
        else (year1_amort.interest_due if year1_amort else None)
    )
    if (
        year1_amort
        and net_profit is not None
        and depreciation is not None
        and interest_on_loan is not None
    ):
        outputs.dscr_pat_dep_interest = dscr(
            net_profit=net_profit,
            depreciation=depreciation,
            interest_on_term_loan=interest_on_loan,
            principal_due=year1_amort.principal_due,
            interest_due=year1_amort.interest_due,
            definition=CadsDefinition.PAT_DEP_INTEREST,
        )
        outputs.dscr_pat_dep = dscr(
            net_profit=net_profit,
            depreciation=depreciation,
            interest_on_term_loan=interest_on_loan,
            principal_due=year1_amort.principal_due,
            interest_due=year1_amort.interest_due,
            definition=CadsDefinition.PAT_DEP,
        )

    if fuel_finding := _fuel_finding(fin):
        findings.append(fuel_finding)
    if crew_finding := _crew_wage_finding(fin):
        findings.append(crew_finding)
    fund_flow_finding, fund_flow_trace = _fund_flow_finding(fin)
    if fund_flow_finding:
        findings.append(fund_flow_finding)
    outputs.fund_flow_gap = fund_flow_trace

    if year1 and year1.tax_paid is not None and year1.profit_before_tax is not None:
        outputs.tax_rate = derive_tax_rate(
            tax_paid=_dec(year1.tax_paid),
            profit_before_tax=_dec(year1.profit_before_tax),
        )

    h = fin.harvester_assumptions
    if (
        h
        and year1_amort
        and depreciation is not None
        and interest_on_loan is not None
        and (price := _extracted(h.price_per_acre)) is not None
        and (variable_corrected := _extracted(h.running_cost_per_acre_corrected))
        is not None
    ):
        fixed_cost_rupees = (depreciation + interest_on_loan) * Decimal(100_000)
        depreciation_rupees = depreciation * Decimal(100_000)
        inputs = BreakEvenInputs(
            price_per_unit=price,
            variable_cost_per_unit=variable_corrected,
            fixed_cost_total=fixed_cost_rupees,
            depreciation_component=depreciation_rupees,
        )
        outputs.breakeven_accounting = accounting_breakeven(inputs)
        outputs.breakeven_operating_cashflow = operating_cashflow_breakeven(inputs)

        if outputs.tax_rate and outputs.tax_rate.computed_value is not None:
            outputs.breakeven_debt_service_threshold = debt_service_threshold_breakeven(
                price_per_unit=price,
                variable_cost_per_unit=variable_corrected,
                fixed_cost_excluding_depreciation_and_interest=Decimal(0),
                depreciation=depreciation_rupees,
                interest_on_term_loan=interest_on_loan * Decimal(100_000),
                tax_rate=outputs.tax_rate.computed_value,
                principal_due=year1_amort.principal_due * Decimal(100_000),
                interest_due=year1_amort.interest_due * Decimal(100_000),
                definition=CadsDefinition.PAT_DEP_INTEREST,
            )

    if h and (acres_stated := _extracted(h.acres_per_year)) is not None:
        feasibility_assumptions = {
            key: value
            for key, extracted in (
                ("acres_per_day", h.acres_per_day),
                ("operating_days_per_season", h.operating_days_per_season),
                ("seasons_per_year", h.seasons_per_year),
            )
            if (value := _extracted(extracted)) is not None
        }
        outputs.feasible_acres_per_year = assess_sector_feasibility(
            "harvester", feasibility_assumptions
        )
        feasible = outputs.feasible_acres_per_year.computed_value
        if (
            outputs.feasible_acres_per_year.status == "COMPUTED"
            and feasible is not None
            and abs(acres_stated - feasible) > MATERIAL_GAP_TOLERANCE
        ):
            feasible_whole = feasible.quantize(Decimal("1"))
            acres_stated_whole = acres_stated.quantize(Decimal("1"))
            findings.append(
                _calculated_finding(
                    check="acres_per_year_feasibility",
                    title="Claimed annual acreage exceeds plausible machine throughput",
                    explanation=(
                        f"{outputs.feasible_acres_per_year.formula} = "
                        f"{feasible_whole} acres/year; the document plans "
                        f"for {acres_stated_whole}."
                    ),
                    page=h.acres_per_year.page,
                    computed_value=feasible_whole,
                    stated_value=acres_stated_whole,
                    unit=" acres/year",
                    # Same category as the narrative sector-feasibility
                    # finding a real agent produces for this same issue, so
                    # finding_consolidation.consolidate() merges them into
                    # one (calculated wins on provenance rank) instead of
                    # showing two near-identical findings.
                    category="assumption",
                )
            )

    if (
        h
        and year1
        and year1.profit_before_tax is not None
        and outputs.tax_rate
        and outputs.tax_rate.computed_value is not None
        and year1_amort
        and depreciation is not None
        and interest_on_loan is not None
        and (acres := _extracted(h.acres_per_year)) is not None
        and (fuel_stated := _extracted(h.fuel_cost_stated)) is not None
        and (fuel_litres := _extracted(h.fuel_litres_per_acre)) is not None
        and (fuel_price := _extracted(h.fuel_price_per_litre)) is not None
        and (crew_stated := _extracted(h.crew_wage_total_p_and_l_stated)) is not None
    ):
        fuel_recomputed = (acres * fuel_litres * fuel_price) / Decimal(100_000)
        crew_roles = [r for r in h.crew_structure if r.status == "extracted"]
        crew_recomputed = sum(
            (
                Decimal(str(r.annual_wage_per_person)) * r.count
                for r in crew_roles
                if r.annual_wage_per_person is not None
            ),
            start=Decimal("0"),
        )
        if crew_recomputed > 0:
            scenarios = run_harvester_scenarios(
                acres=acres,
                pbt_dpr_as_written=_dec(year1.profit_before_tax),
                tax_rate=outputs.tax_rate.computed_value,
                depreciation=depreciation,
                interest_on_term_loan=interest_on_loan,
                principal_due=year1_amort.principal_due,
                interest_due=year1_amort.interest_due,
                fuel_cost_dpr_stated=fuel_stated,
                fuel_cost_recomputed=fuel_recomputed,
                crew_wage_dpr_stated=crew_stated,
                crew_wage_recomputed=crew_recomputed,
            )
            outputs.scenarios = [
                ScenarioOutput(
                    scenario_id=s.scenario_id,
                    label=s.label,
                    is_hypothetical=s.is_hypothetical,
                    source_class=s.source_class,
                    dscr=s.dscr_trace,
                )
                for s in scenarios
            ]

    return outputs, findings
