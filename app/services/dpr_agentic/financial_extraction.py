"""Bounded financial extraction with separately validated source domains.

A monolithic Gemini response schema can legally return `{}`. Grouping fields by
source section makes omission observable, and avoids repeatedly transmitting the
entire document. This module does not guess financial amounts from narratives.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from .context_router import financial_extraction_context, page_sections
from .financial_schema import (
    ProjectFinancials,
    aggregate_repayment_instalments,
    merge_missing_financials,
    normalize_year_numbers,
)
from .gemini import CURRENT_ROLE, GeminiAgentError
from .schedule_parser import parse_instalment_schedule
from .yearly_rows import apply_yearly_rows, parse_yearly_rows

GROUPS: dict[str, frozenset[str]] = {
    "cost_funding": frozenset(
        {"project_cost", "means_of_finance", "vendor_quotation", "working_capital"}
    ),
    "repayment": frozenset({"repayment_schedule", "dscr"}),
    "operating_financials": frozenset(
        {
            "profit_and_loss",
            "cash_flow",
            "balance_sheet",
            "revenue_assumptions",
            "operating_costs",
        }
    ),
    # Optional groups (M3): attempted only when their pages exist, never
    # retried, never reported missing -- not every DPR states per-unit
    # figures, and their absence is not an extraction failure.
    "unit_economics": frozenset(
        {"operations", "revenue_assumptions", "operating_costs"}
    ),
    "harvester": frozenset({"operations", "operating_costs", "revenue_assumptions"}),
    "polyhouse": frozenset(
        {"operations", "project_cost", "operating_costs", "revenue_assumptions"}
    ),
    "dairy": frozenset(
        {"operations", "project_cost", "operating_costs", "revenue_assumptions"}
    ),
    "cold_storage": frozenset(
        {"operations", "project_cost", "operating_costs", "revenue_assumptions"}
    ),
    # The DPR's own IRR, break-even, D:E, FACR, payback, average DSCR and
    # key assumptions, printed next to our recomputed figures (CAM page 3).
    "stated_ratios": frozenset({"ratios", "assumptions", "dscr"}),
}
OPTIONAL_GROUPS = frozenset(
    {
        "unit_economics",
        "harvester",
        "polyhouse",
        "dairy",
        "cold_storage",
        "stated_ratios",
    }
)
# A combine-harvester custom-hiring DPR names its machine; the harvester
# group (and its fuel / crew / acreage checks) runs only then.
_HARVESTER_TERMS = ("combine harvester", "harvester")
# Likewise a protected-cultivation DPR and the polyhouse group.
_POLYHOUSE_TERMS = (
    "polyhouse",
    "poly house",
    "poly-house",
    "greenhouse",
    "green house",
    "shade net",
    "shadenet",
    "nvph",
)
_DAIRY_TERMS = (
    "dairy",
    "milch",
    "murrah",
    "buffaloes",
    "crossbred cow",
    "cross bred cow",
    "milk yield",
)
_COLD_STORAGE_TERMS = ("cold storage", "cold store", "cold chain", "ripening chamber")
# Sector groups and the words that make a DPR one of them.
_SECTOR_TERMS = {
    "harvester": _HARVESTER_TERMS,
    "polyhouse": _POLYHOUSE_TERMS,
    "dairy": _DAIRY_TERMS,
    "cold_storage": _COLD_STORAGE_TERMS,
}

GROUP_PROMPTS = {
    "cost_funding": (
        "Extract ONLY project cost and sources of finance from the provided original DPR pages. "
        "Return itemized costs, stated total, itemized promoter/loan/other funding, "
        "stated funding total and stated term-loan amount. Do not confuse a vendor "
        "quotation with the project-cost table, and never treat conditional subsidies "
        "as promoter contributions. Amounts in financial output are in INR LAKHS; "
        "when the DPR states whole rupees, convert by dividing by 100000. "
        "If the source unit is ambiguous, omit the amount. Do not invent absent values. "
        "For term_loan_amount include its original PDF page number and status. "
        "If the DPR has a fund-flow (sources and applications of funds) "
        "statement, copy its stated sources total and applications total "
        "into fund_flow_sources_total and fund_flow_applications_total, each "
        "with its page and status; leave them missing otherwise. "
        "If there are contradictory source figures, do not silently reconcile them."
    ),
    "repayment": (
        "Transcribe ONLY the loan terms, repayments and reported DSCR from the "
        "attached original DPR pages. Never add up, average or convert figures "
        "yourself. If the document states YEARLY principal and interest totals "
        "(e.g. a yearly repayment or DSCR table), copy them into years[] or "
        "repayment_table in INR LAKHS (whole rupees divided by 100000). If it "
        "only prints a MONTHLY, QUARTERLY or HALF-YEARLY schedule, do not "
        "compute yearly totals: copy every instalment row exactly as printed "
        "into repayment_instalments (loan_year as the document labels it, "
        "e.g. 'Year - I' is 1; period label; instalment, interest and principal "
        "as printed, including negative principal), and set "
        "repayment_amount_unit to the unit the amounts are printed in "
        "('rupee' or 'lakh'). Number years 1, 2, 3 in the document's order, "
        "never as calendar years. Distinguish reported DSCR from a calculated "
        "ratio. A row giving the whole yearly instalment -- principal and "
        "interest together, e.g. 'TL Installment', 'EMI', 'Instalment' -- "
        "goes in total_instalment, never in principal_repayment; use "
        "principal_repayment only for a row printed as principal or as "
        "repayment of the loan. "
        "Do not assume an interest rate or invent a missing payment. "
        "Set page and status for each quoted value."
    ),
    "operating_financials": (
        "Transcribe ONLY year-wise revenue, expenses, PBT, tax, profit, depreciation "
        "and term-loan interest found in the supplied financial statements. "
        "Copy each figure exactly as printed on its own line; never add, "
        "subtract or re-derive one. operating_expenses is the statement's own "
        "total operating/running cost line as printed, even if that total "
        "includes depreciation; if no such total line is printed, leave it "
        "null. Set profit_before_tax only from a line printed as profit "
        "before tax, and net_profit from the profit after tax (or net profit) "
        "line; never copy one into the other. "
        "Use INR LAKHS for monetary amounts, converting whole rupees by dividing "
        "by 100000. Number projection years 1, 2, 3 in the order the "
        "statements present them (the first projected year is 1), never as "
        "calendar or financial years such as 2026 or 25-26; do not fill "
        "missing years with hypothetical numbers."
    ),
    "unit_economics": (
        "Transcribe ONLY the operating assumptions behind the revenue: what "
        "one unit of output or service is (unit_name, e.g. 'acre', 'litre', "
        "'tonne', 'hour'), capacity_per_day, operating_days_per_year, "
        "capacity_per_year (full capacity, if printed), price_per_unit_rupees "
        "(rate charged per unit) and variable_cost_per_unit_rupees (running "
        "cost per unit, if printed; DPRs also call it 'variable cost per "
        "unit', 'raw material cost per unit', 'cost of production per unit', "
        "'operating cost per acre / hour / litre'), and for each projection "
        "year the capacity utilisation percentage and/or units sold (often a "
        "'capacity utilisation' or 'level of operation' row, e.g. 60%, 70%, "
        "80%, above the profit and loss projection). Copy each figure "
        "exactly as printed with its page; never multiply, add or derive one. "
        "Prices and per-unit costs stay in RUPEES per unit, as printed. Every "
        "figure must refer to the same unit_name; leave a figure missing if "
        "the document states it in a different unit. Number projection years "
        "1, 2, 3 in the document's order, never as calendar years. Leave "
        "unit_economics null if the document states none of these."
    ),
    "harvester": (
        "This DPR is for a combine-harvester custom hiring centre. Transcribe "
        "ONLY harvester_assumptions, each figure exactly as printed with its "
        "page and status ('missing' when not stated; never guess): "
        "acres_per_year, acres_per_day, operating_days_per_season, "
        "seasons_per_year, price_per_acre (the rental rate charged per acre, "
        "in rupees), running_cost_per_acre_stated (the DPR's own headline "
        "running cost per acre), running_cost_per_acre_corrected (a different "
        "running-cost-per-acre figure given elsewhere in the same document, "
        "e.g. a cost-breakdown annexure; missing if there is only one), "
        "fuel_litres_per_acre, fuel_price_per_litre, fuel_cost_stated (the "
        "P&L's annual fuel cost, in INR LAKHS), crew_wage_total_p_and_l_stated "
        "(the P&L's annual crew wages, in INR LAKHS), and crew_structure (one "
        "entry per staffing role with role, count and annual_wage_per_person "
        "in INR LAKHS, each with its page and status). Never add up figures."
    ),
    "polyhouse": (
        "This DPR is for protected cultivation (a polyhouse, greenhouse or "
        "shade-net house). Transcribe ONLY polyhouse_assumptions, each figure "
        "exactly as printed with its page and status ('missing' when not "
        "stated; never guess or derive): structure_type (as printed, e.g. "
        "'NVPH', 'naturally ventilated', 'fan and pad', 'shade net'), crop "
        "(the main crop, as printed), produce_unit (what is sold: 'stem', "
        "'bunch', 'kg'...), area_sq_m (covered area in square metres), "
        "structure_cost_lakh (the cost of the polyhouse structure alone, not "
        "the whole project, in INR LAKHS; whole rupees divided by 100000), "
        "plants (number of plants, only if printed as a number of plants), "
        "yield_per_sq_m_per_year and yield_per_plant_per_year (only if "
        "printed), units_per_year (the annual production at full capacity, "
        "only if printed as a quantity, never a rupee amount), and "
        "price_per_unit_rupees (the sale price per produce_unit, in rupees). "
        "Never multiply, add or divide figures."
    ),
    "dairy": (
        "This DPR is for a dairy unit. Transcribe ONLY dairy_assumptions, "
        "each figure exactly as printed with its page and status ('missing' "
        "when not stated; never guess or derive): animal_type (breed or type "
        "as printed, e.g. 'HF crossbred cow', 'Murrah buffalo'), animals "
        "(number of milch animals), milk_yield_litres_per_day (per animal per "
        "day while in milk), lactation_days (days in milk per lactation or "
        "per year, as printed, e.g. a 'Lactation days' or 'Lactation period' "
        "row), milk_per_year_litres (the herd's annual milk, only if printed "
        "as litres), milk_price_per_litre (sale price in rupees per litre, "
        "e.g. a 'Selling price of milk' or 'Milk rate' row), and "
        "feed_cost_per_animal_per_day (in rupees, only if printed). Figures "
        "in an assumptions or parameters table count as printed. Never "
        "multiply, add or divide figures."
    ),
    "cold_storage": (
        "This DPR is for a cold storage. Transcribe ONLY "
        "cold_storage_assumptions, each figure exactly as printed with its "
        "page and status ('missing' when not stated; never guess or derive): "
        "storage_type (as printed, e.g. 'single chamber', 'multi-chamber', "
        "'PEB', 'controlled atmosphere'), commodity (main commodity stored, "
        "e.g. 'potato'), capacity_mt (storage capacity in metric tonnes), "
        "capital_cost_lakh (the cold storage's capital cost -- building, "
        "insulation and refrigeration plant, without land or working capital "
        "-- in INR LAKHS; whole rupees divided by 100000), "
        "chamber_volume_cubic_m (total chamber volume in cubic metres, only "
        "if printed as a volume), rent_per_quintal_rupees (storage rent per "
        "quintal in rupees, as printed; if printed per tonne or per bag leave "
        "it missing) and rent_basis ('season' or 'month', as printed). Never "
        "multiply, add or divide figures."
    ),
    "stated_ratios": (
        "Transcribe ONLY the DPR's own summary ratios and key assumptions "
        "into stated_ratios, each figure exactly as printed with its page and "
        "status ('missing' when not stated; never compute, average or "
        "estimate one): irr_pct (the project IRR in percent; if only an "
        "equity IRR is printed, leave it missing), average_dscr (the DSCR "
        "printed as the average, never one year's), break_even_pct (the "
        "break-even point as a percentage of capacity or sales) with "
        "break_even_basis as printed ('cash', 'accounting'...), debt_equity "
        "(a ratio printed '2.33:1' is 2.33), fixed_asset_coverage (FACR), "
        "payback_years (only if printed as a number of years, e.g. 4.5), "
        "price_escalation_pct and cost_escalation_pct (yearly increase in "
        "selling price and in costs, in percent), depreciation_method as "
        "printed ('SLM', 'WDV', 'straight line'...) and depreciation_rate_pct "
        "(the main plant and machinery rate, in percent). Percentages are "
        "plain numbers: 15.5 for 15.5%. Leave stated_ratios null if the "
        "document states none of these."
    ),
}


@dataclass(frozen=True)
class ExtractionAudit:
    attempted: tuple[str, ...]
    failed: tuple[str, ...]
    empty: tuple[str, ...]
    absent_in_source: tuple[str, ...]
    recovered: tuple[str, ...]


def _has_group_data(group: str, result: ProjectFinancials) -> bool:
    if group == "unit_economics":
        return result.unit_economics is not None
    if group == "harvester":
        return result.harvester_assumptions is not None
    if group == "polyhouse":
        return result.polyhouse_assumptions is not None
    if group == "dairy":
        return result.dairy_assumptions is not None
    if group == "cold_storage":
        return result.cold_storage_assumptions is not None
    if group == "stated_ratios":
        return result.stated_ratios is not None
    if group == "cost_funding":
        return any(
            (
                result.cost_of_project_components,
                result.means_of_finance_components,
                result.cost_of_project_stated_total is not None,
                result.means_of_finance_stated_total is not None,
                result.term_loan_amount.value is not None,
            )
        )
    if group == "repayment":
        return bool(result.repayment_table or result.repayment_instalments) or any(
            any(
                (
                    y.principal_repayment is not None,
                    y.interest_repayment is not None,
                    y.stated_dscr is not None,
                )
            )
            for y in result.years
        )
    return any(
        any(
            (
                y.revenue is not None,
                y.operating_expenses is not None,
                y.net_profit is not None,
                y.depreciation is not None,
            )
        )
        for y in result.years
    )


def _operating_complete(result: ProjectFinancials) -> bool:
    """The DSCR needs net profit and depreciation; revenue alone is not a
    usable P&L. Seen live: one run returned only revenue, passed as
    complete, and the report silently had no DSCR."""
    return any(
        y.net_profit is not None and y.depreciation is not None for y in result.years
    )


def _repayment_complete(result: ProjectFinancials) -> bool:
    """Debt service needs the principal. Seen live: one run read a DPR's
    DSCR statement -- stated DSCR and interest -- but not its principal
    row, passed as complete, and the report silently had no DSCR."""
    result = aggregate_repayment_instalments(result)
    return any(
        y.principal_repayment is not None or y.total_instalment is not None
        for y in result.years
    ) or any(
        r.principal_due.status == "extracted" and r.principal_due.value is not None
        for r in result.repayment_table
    )


def _group_complete(group: str, result: ProjectFinancials) -> bool:
    if group == "operating_financials":
        return _operating_complete(result)
    if group == "repayment":
        return _repayment_complete(result)
    if group != "cost_funding":
        return _has_group_data(group, result)
    has_cost = (
        bool(result.cost_of_project_components)
        or result.cost_of_project_stated_total is not None
    )
    has_financing = bool(result.means_of_finance_components) or (
        result.term_loan_amount.status == "extracted"
        and result.term_loan_amount.value is not None
    )
    return has_cost and has_financing


def merge_section_financials(
    base: ProjectFinancials, incoming: ProjectFinancials
) -> ProjectFinancials:
    """Merge year rows by year ID, never discard complementary schedule fields."""
    merged = merge_missing_financials(base, incoming)
    existing = {y.year: y.model_copy(deep=True) for y in base.years}
    for y in incoming.years:
        if y.year not in existing:
            existing[y.year] = y.model_copy(deep=True)
            continue
        row = existing[y.year]
        for name in type(y).model_fields:
            if name == "year":
                continue
            if getattr(row, name) is None and getattr(y, name) is not None:
                setattr(row, name, getattr(y, name))
    merged.years = [existing[i] for i in sorted(existing)]
    return merged


SCHEDULE_ALREADY_READ = (
    " The per-instalment repayment schedule in these pages has already been "
    "read and checked by code: leave repayment_instalments empty and do not "
    "transcribe or total its rows. Extract only the other fields."
)
RECOVERY_BUDGET = 2  # hard ceiling per DPR, even when every domain is empty
RETRY_SUFFIX = (
    " Focus on financial fields still absent; return only source-stated values."
)
# Added to the repayment retry: the field the first attempt most often
# misses is the principal row of a DSCR statement laid out in columns.
REPAYMENT_RETRY_HINT = (
    " The previous attempt returned no principal repayment. If the DPR prints "
    "the principal repaid per year anywhere -- often a 'Repayment of term "
    "loan', 'Loan repayment' or 'Principal' row in the DSCR statement -- copy "
    "it into principal_repayment for each year, exactly as printed."
)


def _with_schedule(result: ProjectFinancials, schedule, rows=None) -> ProjectFinancials:
    """`result` with what code read from the pages: the instalment schedule
    (schedule_parser.py) and the DSCR-statement rows (yearly_rows.py)."""
    if schedule is not None:
        result = result.model_copy(
            update={
                "repayment_instalments": schedule.instalments,
                "repayment_amount_unit": schedule.unit,
            }
        )
    return apply_yearly_rows(result, rows or {})


async def _call_group(
    llm: Any, group: str, prompt: str, ctx: dict
) -> ProjectFinancials | None:
    """One extraction call under its own role label; None on a Gemini error.
    Each concurrent call sets CURRENT_ROLE inside its own task, so token
    usage is still attributed per domain."""
    role_token = CURRENT_ROLE.set("financial_" + group)
    try:
        # Renumber calendar years per domain, before any merge by year.
        return normalize_year_numbers(
            await llm.extract_group(prompt, {"dpr": ctx}, group)
        )
    except GeminiAgentError:
        return None
    finally:
        CURRENT_ROLE.reset(role_token)


async def extract_financial_sections(
    llm: Any, pages: list[Any], project_context: dict
) -> tuple[ProjectFinancials, ExtractionAudit]:
    """At most one call per source domain plus one targeted retry per failed domain.

    Missing section keywords do not prove that a table is absent: if no
    financial headings are recognized, cost/funding and repayment still run
    against a conservative bounded document sample.

    Runs in two concurrent phases -- every domain's first attempt at once,
    then the retries at once -- so latency is at most two Gemini round trips
    instead of up to six sequential ones. Which domains get one of the
    RECOVERY_BUDGET retries is decided in fixed GROUPS order after phase one,
    never by which call happened to return first, so the same DPR always
    takes the same path. Results merge in GROUPS order for the same reason.
    """
    from types import SimpleNamespace

    inp = SimpleNamespace(pages=pages, project_context=project_context)
    discovered = {section for page in pages for section in page_sections(page.text)}
    attempts: list[str] = []
    failed: list[str] = []
    empty: list[str] = []
    absent: list[str] = []
    recovered: list[str] = []
    output = ProjectFinancials()
    # A per-instalment schedule is read in code when its rows check out
    # (schedule_parser.py); Gemini is then told not to transcribe it.
    schedule = parse_instalment_schedule(pages)
    yearly = parse_yearly_rows(pages)

    # Phase 1 setup: decide each domain's context (no network).
    planned: dict[str, tuple[set[str], dict]] = {}
    folded = [page.text.casefold() for page in pages]
    sectors = {
        group
        for group, terms in _SECTOR_TERMS.items()
        if any(term in text for text in folded for term in terms)
    }
    for group, sections in GROUPS.items():
        relevant = discovered & sections
        if group in OPTIONAL_GROUPS:
            if not relevant or (group in _SECTOR_TERMS and group not in sectors):
                continue
            ctx = financial_extraction_context(
                inp, sections=set(relevant), page_limit=7
            )
            attempts.append(group)
            planned[group] = (set(relevant), ctx)
            continue
        if not relevant and group == "operating_financials":
            absent.append(group)
            continue
        if not relevant:
            # No recognized repayment/cost heading is NOT proof that the
            # underlying statement is absent (broken OCR or atypical layout).
            # Make one conservative attempt; unresolved evidence is still
            # flagged for manual or targeted recovery.
            absent.append("unindexed_" + group)
        # The page cap and text-size cap are explicit; a truncated sample must
        # remain diagnosable rather than pretending the original DPR is empty.
        chosen_sections = set(relevant or sections)
        ctx = financial_extraction_context(inp, sections=chosen_sections, page_limit=7)
        if not relevant:
            # Span the document instead of sending only its first pages.
            # Include table-heavy middle pages plus first/last pages.
            ordered = sorted(pages, key=lambda page: page.page)
            sampling = {page.page: page for page in ordered[:2] + ordered[-2:]}
            ranked = sorted(
                ordered,
                key=lambda page: sum(str(c).isdigit() for c in page.text[:2500]),
                reverse=True,
            )
            for page in ranked:
                if len(sampling) >= 7:
                    break
                sampling[page.page] = page
            ctx["pages"] = [
                {"page": p.page, "text": p.text}
                for p in sorted(sampling.values(), key=lambda p: p.page)
            ]
            ctx["partial_source"] = len(sampling) < len(pages)
        attempts.append(group)
        planned[group] = (chosen_sections, ctx)

    # Phase 1: every domain's first attempt, concurrently.
    def prompt_for(group: str) -> str:
        if group == "repayment" and schedule is not None:
            return GROUP_PROMPTS[group] + SCHEDULE_ALREADY_READ
        return GROUP_PROMPTS[group]

    first = await asyncio.gather(
        *(
            _call_group(llm, group, prompt_for(group), ctx)
            for group, (_, ctx) in planned.items()
        )
    )

    # Merge in GROUPS order; pick retries in GROUPS order.
    retries: list[tuple[str, dict]] = []
    budget = RECOVERY_BUDGET
    for group, part in zip(planned, first, strict=True):
        if group in OPTIONAL_GROUPS:
            if part is not None and _has_group_data(group, part):
                output = merge_section_financials(output, part)
            continue
        if part is None:
            failed.append(group)
            continue
        if _has_group_data(group, part):
            output = merge_section_financials(output, part)
        if _group_complete(group, _with_schedule(part, schedule, yearly)):
            continue
        empty.append(group)
        if budget <= 0:
            continue
        budget -= 1
        # One bounded retry only for this source domain. Do not resend the
        # giant schema or the full DPR on retry.
        chosen_sections = planned[group][0]
        retry_sections = set(chosen_sections)
        if group == "cost_funding":
            retry_sections = set()
            if not (
                part.cost_of_project_stated_total is not None
                or part.cost_of_project_components
            ):
                retry_sections |= {"project_cost", "vendor_quotation"}
            if not (
                part.means_of_finance_components
                or part.term_loan_amount.value is not None
            ):
                retry_sections |= {"means_of_finance"}
            retry_sections &= discovered
        retries.append(
            (
                group,
                financial_extraction_context(
                    inp, sections=retry_sections or set(chosen_sections), page_limit=5
                ),
            )
        )

    # Phase 2: the selected retries, concurrently.
    second = await asyncio.gather(
        *(
            _call_group(
                llm,
                group,
                prompt_for(group)
                + (REPAYMENT_RETRY_HINT if group == "repayment" else "")
                + RETRY_SUFFIX,
                ctx,
            )
            for group, ctx in retries
        )
    )
    for (group, _), part in zip(retries, second, strict=True):
        if part is None:
            failed.append(group)
            continue
        if _has_group_data(group, part):
            output = merge_section_financials(output, part)
            recovered.append(group)

    # A group's first response can be incomplete and its retry then fill
    # it; report only groups still incomplete in the merged result, so a
    # recovered group never surfaces as an "empty response" question.
    # The code-read schedule replaces anything the model transcribed.
    output = aggregate_repayment_instalments(_with_schedule(output, schedule, yearly))
    output = verify_unit_economics_pages(output, pages)
    output = verify_sector_pages(output, pages)
    still_empty = [g for g in empty if not _group_complete(g, output)]
    return output, ExtractionAudit(
        tuple(attempts),
        tuple(failed),
        tuple(still_empty),
        tuple(absent),
        tuple(recovered),
    )


def _number_forms(value: float) -> list[str]:
    """Ways a figure can be printed: 2000 / 2000.0 / 2000.00; 7.5 / 7.50."""
    forms = {f"{value:.2f}".rstrip("0").rstrip("."), f"{value:.2f}", f"{value:.1f}"}
    if float(value).is_integer():
        forms.add(str(int(value)))
    return [f for f in forms if f]


def figure_on_page(value: float | None, page: int | None, pages_by_no: dict) -> bool:
    """True when `value` is printed on `page` (thousands separators ignored,
    Indian 1,00,000 grouping included). Used to drop operating figures the
    model attributes to a page that does not show them."""
    if value is None or page is None or page not in pages_by_no:
        return False
    text = pages_by_no[page].replace(",", "")
    return any(
        re.search(rf"(?<![\d.]){re.escape(form)}(?![\d])", text)
        for form in _number_forms(value)
    )


def verify_unit_economics_pages(
    output: ProjectFinancials, pages: list
) -> ProjectFinancials:
    """Every unit-economics figure must appear on the page it cites; one
    that does not is marked missing (single values) or dropped (year rows),
    never kept on the model's word. These figures drive must-fix findings,
    so an unverifiable one is worse than none."""
    ue = output.unit_economics
    if ue is None:
        return output
    pages_by_no = {p.page: p.text for p in pages}
    fixed = output.model_copy(deep=True)
    ue = fixed.unit_economics
    for key in (
        "capacity_per_day",
        "operating_days_per_year",
        "capacity_per_year",
        "price_per_unit_rupees",
        "variable_cost_per_unit_rupees",
    ):
        ev = getattr(ue, key)
        on_page = figure_on_page(ev.value, ev.page, pages_by_no)
        if ev.status == "extracted" and not on_page:
            setattr(ue, key, ev.model_copy(update={"value": None, "status": "missing"}))
    kept = []
    for y in ue.years:
        if y.utilisation_pct is not None and not figure_on_page(
            y.utilisation_pct, y.page, pages_by_no
        ):
            y.utilisation_pct = None
        if y.units_sold is not None and not figure_on_page(
            y.units_sold, y.page, pages_by_no
        ):
            y.units_sold = None
        if y.utilisation_pct is not None or y.units_sold is not None:
            kept.append(y)
    ue.years = kept
    return fixed


# Per sector group: (field, its figures, figures that may be printed in
# whole rupees while extracted in lakh).
_SECTOR_FIGURES = {
    "polyhouse_assumptions": (
        (
            "area_sq_m",
            "structure_cost_lakh",
            "plants",
            "yield_per_sq_m_per_year",
            "yield_per_plant_per_year",
            "units_per_year",
            "price_per_unit_rupees",
        ),
        {"structure_cost_lakh"},
    ),
    "dairy_assumptions": (
        (
            "animals",
            "milk_yield_litres_per_day",
            "lactation_days",
            "milk_per_year_litres",
            "milk_price_per_litre",
            "feed_cost_per_animal_per_day",
        ),
        set(),
    ),
    "cold_storage_assumptions": (
        (
            "capacity_mt",
            "capital_cost_lakh",
            "chamber_volume_cubic_m",
            "rent_per_quintal_rupees",
        ),
        {"capital_cost_lakh"},
    ),
    "stated_ratios": (
        (
            "irr_pct",
            "average_dscr",
            "break_even_pct",
            "debt_equity",
            "fixed_asset_coverage",
            "payback_years",
            "price_escalation_pct",
            "cost_escalation_pct",
            "depreciation_rate_pct",
        ),
        set(),
    ),
}


def verify_sector_pages(output: ProjectFinancials, pages: list) -> ProjectFinancials:
    """Like verify_unit_economics_pages, for the polyhouse, dairy and cold
    storage groups: a figure must appear on the page it cites, or it is
    marked missing. Capital costs may be printed in lakh or whole rupees
    (the model converts the latter)."""
    if all(getattr(output, f) is None for f in _SECTOR_FIGURES):
        return output
    pages_by_no = {p.page: p.text for p in pages}
    fixed = output.model_copy(deep=True)
    for field_name, (keys, lakh_keys) in _SECTOR_FIGURES.items():
        model = getattr(fixed, field_name)
        if model is None:
            continue
        for key in keys:
            ev = getattr(model, key)
            if ev.status != "extracted":
                continue
            on_page = figure_on_page(ev.value, ev.page, pages_by_no) or (
                key in lakh_keys
                and ev.value is not None
                and figure_on_page(round(ev.value * 100_000), ev.page, pages_by_no)
            )
            if not on_page:
                setattr(
                    model,
                    key,
                    ev.model_copy(update={"value": None, "status": "missing"}),
                )
    return fixed


# Kept for callers and tests written before the dairy / cold-storage groups.
verify_polyhouse_pages = verify_sector_pages
