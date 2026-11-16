"""What the 2.x "DPR Pre-submission Review" layout prints that the 1.x
snapshot did not carry: the readiness score, the recomputed-figures table,
the documents checklist, the re-review date and the report reference.

Built once, into the frozen CAMSnapshot (cam_adapter.build_snapshot_from_
analysis), from what the analysis already produced -- the code checks
(AnalysisResult.calculations), the checklist answers, the extracted
financials and the scheme checks. Nothing here calls Gemini or invents a
figure: a value the analysis did not produce is left None and printed as
"Pending".
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from decimal import Decimal

from .cam_snapshot import (
    AssumptionRow,
    DocumentStatus,
    ReviewMeasure,
    SchemeColumn,
    SchemeGap,
    SchemeReview,
    StressTest,
)
from .schemas import AnalysisResult

# A minimum yearly DSCR below this is flagged on the recomputed-figures
# page (the design's bank baseline, 1.10-1.20; the lower end is used).
MIN_DSCR_BASELINE = 1.10
# The design's other bank baselines (C4 board). Debt:equity rises to what
# the scheme's minimum own contribution allows (10% own share = 9:1).
MAX_DEBT_EQUITY = 3.0
MIN_FIXED_ASSET_COVERAGE = 1.25
MAX_CASH_BREAK_EVEN_PCT = 65.0
# Capacity utilisation a bank usually accepts for a new unit, years 1-3.
# LAS reference, approved 2026-11-13 as provisional pending credit review
# (the common 60/70/80 ramp-up of term-loan appraisal notes), not a
# regulation.
UTILISATION_RAMP_UP = (60.0, 70.0, 80.0)
# Depreciation references printed in the assumptions table: Income Tax
# Rules, Appendix I (plant and machinery 15% WDV; buildings 10% WDV) and
# Companies Act 2013 Schedule II (plant 15-year useful life, about 6.33%
# a year straight line).
IT_PLANT_WDV_PCT = 15.0
# RBI's inflation target (CPI 4%, band 2-6%), for cost escalation.
INFLATION_TARGET_PCT = 4.0


def report_reference(row_id: int, generated_at: datetime) -> str:
    """The printed reference, PSR/<year>/<row number>: the stored report
    row's own number (dpr_report_artifacts.id), unique and never reused."""
    return f"PSR/{generated_at.year}/{row_id:06d}"


def re_review_by(generated_at: datetime, days: int) -> datetime:
    return generated_at + timedelta(days=days)


# --- Readiness score -------------------------------------------------------


def _family(check: str) -> str:
    """dscr_year_3 -> dscr; revenue_rebuild_year_1 -> revenue_rebuild;
    dscr_stress_price_10 -> dscr_stress_price: one check per family."""
    return re.sub(r"_(?:year_)?\d+$", "", check)


def checklist_answers(result: AnalysisResult) -> dict[str, str]:
    """{check_id: status} for the real checklists.py items each role was
    asked (an id a role was not asked is ignored)."""
    from .checklists import CHECKLISTS

    asked = {role: {i.id for i in items} for role, items in CHECKLISTS.items()}
    out: dict[str, str] = {}
    for role, output in result.agent_outputs.items():
        for answer in output.checks:
            if answer.check_id in asked.get(role, ()):
                out.setdefault(answer.check_id, answer.status)
    return out


def readiness_score(result: AnalysisResult) -> tuple[int | None, int, int]:
    """(percent, passed, total) over every check that reached a verdict: each
    code-check family passes when all its results match; each checklist item
    passes when answered "ok" and fails when answered "issue" (items not
    applicable or undecided are left out). None when nothing was checked."""
    from .financial_calc import MEASURE_CHECKS

    families: dict[str, bool] = {}
    for calc in result.calculations:
        if calc.get("check") in MEASURE_CHECKS:
            continue
        name = _family(str(calc.get("check", "")))
        families[name] = families.get(name, True) and bool(calc.get("match"))
    passed = sum(families.values())
    total = len(families)
    from .checklists import CHECKLISTS

    # Documents-table questions make no finding, so they do not score.
    report_only = {
        i.id for items in CHECKLISTS.values() for i in items if i.report_only
    }
    for check_id, status in checklist_answers(result).items():
        if check_id in report_only:
            continue
        if status in ("ok", "issue"):
            total += 1
            passed += status == "ok"
    if total == 0:
        return None, 0, 0
    return round(passed / total * 100), passed, total


# --- Your figures, recomputed ------------------------------------------------


def _num(value: float | None, places: int = 2) -> str | None:
    if value is None:
        return None
    return f"{value:,.{places}f}"


def _calc(result: AnalysisResult, check: str) -> dict | None:
    return next((c for c in result.calculations if c.get("check") == check), None)


def _financials(result: AnalysisResult):
    from .financial_schema import ProjectFinancials

    return (
        ProjectFinancials.model_validate(result.extracted_financials)
        if result.extracted_financials
        else ProjectFinancials()
    )


def _status(value: float | None, ok) -> str:
    if value is None:
        return "pending"
    return "meets" if ok(value) else "below"


def financial_review(result: AnalysisResult) -> list[ReviewMeasure]:
    """The eleven measures of the recomputed-figures page: the DPR's own
    figure, ours (AnalysisResult.calculations) and the bank baseline. A
    figure the analysis could not compute stays "pending"."""
    from .schemes import own_contribution, schemes_claimed, subsidy_claimed

    fin = _financials(result)
    rows: list[ReviewMeasure] = []

    cost = fin.cost_of_project_stated_total
    cost_check = _calc(result, "cost_of_project_matches_components")
    rows.append(
        ReviewMeasure(
            measure="Total project cost (₹ lakh)",
            dpr_value=_num(cost),
            recomputed=_num(cost_check and cost_check.get("computed_value")),
            baseline="Within unit-cost norms",
            status=(
                "pending"
                if cost_check is None
                else "meets"
                if cost_check.get("match")
                else "below"
            ),
        )
    )

    loan = fin.term_loan_amount
    rows.append(
        ReviewMeasure(
            measure="Term loan (₹ lakh)",
            dpr_value=_num(loan.value if loan.status == "extracted" else None),
            baseline="≤ scheme ceiling",
        )
    )

    claimed = next(
        (
            f.value
            for out in result.agent_outputs.values()
            for f in out.document_facts
            if f.field == "scheme_claimed"
        ),
        None,
    )
    rules = schemes_claimed(claimed)
    minimum = max(
        (r.own_contribution_min_pct for r in rules if r.own_contribution_min_pct),
        default=None,
    )
    own = own_contribution(fin)
    share = own / cost * 100 if own is not None and cost else None
    rows.append(
        ReviewMeasure(
            measure="Your contribution (%)",
            dpr_value=_num(share, 1),
            recomputed=_num(share, 1),
            baseline=f"≥ {minimum:g}% (scheme minimum)"
            if minimum
            else "≥ scheme minimum",
            status=(
                "pending"
                if share is None or minimum is None
                else "meets"
                if share >= minimum
                else "below"
            ),
        )
    )

    subsidy = subsidy_claimed(fin)
    most = max(
        (
            c["max_subsidy_lakh"]
            for c in result.scheme_checks
            if c.get("max_subsidy_lakh") is not None
        ),
        default=None,
    )
    rows.append(
        ReviewMeasure(
            measure="Subsidy (₹ lakh)",
            dpr_value=_num(subsidy),
            recomputed=_num(most),
            baseline="As per scheme",
            status=(
                "pending"
                if subsidy is None or most is None
                else "meets"
                if subsidy <= most * 1.01
                else "below"
            ),
        )
    )

    stated = [y.stated_dscr for y in fin.years if y.stated_dscr is not None]
    printed_average = _stated_ratio(fin, "average_dscr")
    if printed_average is None and len(stated) >= 2:
        printed_average = sum(stated) / len(stated)
    average = _calc(result, "dscr_average")
    rows.append(
        ReviewMeasure(
            measure="Average DSCR",
            dpr_value=_num(printed_average),
            recomputed=_num(average and average.get("computed_value")),
            baseline="≥ 1.25",
            status=(
                "pending"
                if average is None
                else "meets"
                if average.get("match")
                else "below"
            ),
        )
    )
    yearly = [
        c["computed_value"]
        for c in result.calculations
        if re.fullmatch(r"dscr_year_\d+", str(c.get("check", "")))
        and c.get("computed_value") is not None
    ]
    low = min(yearly) if yearly else None
    rows.append(
        ReviewMeasure(
            measure="Minimum DSCR",
            dpr_value=_num(min(stated)) if stated else None,
            recomputed=_num(low),
            baseline="≥ 1.10–1.20",
            status=(
                "pending"
                if low is None
                else "meets"
                if low >= MIN_DSCR_BASELINE
                else "below"
            ),
        )
    )
    rows.extend(_ratio_rows(result, fin, minimum))
    return rows


def _stated_ratio(fin, key: str) -> float | None:
    ratios = fin.stated_ratios
    if ratios is None:
        return None
    ev = getattr(ratios, key)
    return ev.value if ev.status == "extracted" else None


def _value(result: AnalysisResult, check: str) -> float | None:
    calc = _calc(result, check)
    return calc.get("computed_value") if calc else None


def _ratio_rows(result, fin, own_minimum: float | None) -> list[ReviewMeasure]:
    """IRR, cash break-even, debt:equity, fixed asset coverage and payback
    (financial_ratios.py) against the bank baselines."""
    rows = []

    irr = _value(result, "project_irr")
    rate = fin.effective_interest_rate_pct or fin.standard_interest_rate_pct
    rows.append(
        ReviewMeasure(
            measure="Project IRR (%)",
            dpr_value=_num(_stated_ratio(fin, "irr_pct"), 1),
            recomputed=_num(irr, 1),
            baseline=f"> {rate:g}% (your loan rate)" if rate else "> cost of capital",
            status="pending" if rate is None else _status(irr, lambda v: v > rate),
        )
    )

    bep = _value(result, "cash_break_even")
    rows.append(
        ReviewMeasure(
            measure="Cash break-even (% capacity)",
            dpr_value=(
                f"{printed_bep:.1f} ({basis[:20].lower()})"
                if (printed_bep := _stated_ratio(fin, "break_even_pct")) is not None
                and (basis := fin.stated_ratios.break_even_basis)
                else _num(printed_bep, 1)
            ),
            recomputed=_num(bep, 1),
            baseline="≤ 60–65%",
            status=_status(bep, lambda v: v <= MAX_CASH_BREAK_EVEN_PCT),
        )
    )

    allowed = MAX_DEBT_EQUITY
    if own_minimum:
        allowed = max(allowed, round((100 - own_minimum) / own_minimum, 2))
    de = _value(result, "debt_equity")
    printed_de = _stated_ratio(fin, "debt_equity")
    rows.append(
        ReviewMeasure(
            measure="Debt : equity",
            dpr_value=f"{printed_de:.2f} : 1" if printed_de is not None else None,
            recomputed=f"{de:.2f} : 1" if de is not None else None,
            baseline=f"≤ {allowed:g}:1"
            + (f" ({own_minimum:g}% own share)" if allowed > MAX_DEBT_EQUITY else ""),
            status=_status(de, lambda v: v <= allowed + 0.005),
        )
    )

    facr = _calc(result, "fixed_asset_coverage")
    facr_value = facr.get("computed_value") if facr else None
    rows.append(
        ReviewMeasure(
            measure="Fixed asset coverage",
            dpr_value=_num(_stated_ratio(fin, "fixed_asset_coverage")),
            recomputed=(
                f"{facr_value:.2f} ({facr['note']})"
                if facr_value is not None and facr.get("note")
                else _num(facr_value)
            ),
            baseline="≥ 1.25–1.33",
            status=_status(facr_value, lambda v: v >= MIN_FIXED_ASSET_COVERAGE),
        )
    )

    payback = _calc(result, "payback_years")
    tenure = fin.loan_tenor_years
    tenure = tenure.value if tenure.status == "extracted" else None
    if payback is None:
        shown, status = None, "pending"
    elif payback.get("computed_value") is None:
        # Not recovered within the projection years.
        shown = (payback.get("note") or "beyond the projection").replace(
            "beyond ", "> "
        )
        status = "below"
    else:
        shown = _num(payback["computed_value"], 1)
        status = (
            "pending"
            if tenure is None
            else _status(payback["computed_value"], lambda v: v < tenure)
        )
    rows.append(
        ReviewMeasure(
            measure="Payback period (years)",
            dpr_value=_num(_stated_ratio(fin, "payback_years"), 1),
            recomputed=shown,
            baseline=f"< loan tenure ({tenure:g} yrs)" if tenure else "< loan tenure",
            status=status,
        )
    )
    return rows


# --- Assumptions to revise ---------------------------------------------------


def _rupees(value: float) -> str:
    return f"₹ {value:,.2f}".rstrip("0").rstrip(".")


def _range_row(name: str, ref: dict | None, money: bool) -> AssumptionRow | None:
    """A row from a benchmark-table reference (benchmarks._references):
    above the range's top, the top is suggested."""
    if not ref:
        return None
    fmt = _rupees if money else (lambda v: f"{v:g}")
    unit = f" per {ref['unit']}" if money else f" {ref['unit']}"
    over = ref["dpr"] > ref["high"] * 1.0001
    low = f"{ref['low']:,g}" if money else fmt(ref["low"])
    return AssumptionRow(
        assumption=name,
        dpr_value=fmt(ref["dpr"]) + unit,
        reference=f"{'₹ ' if money else ''}{low}–{ref['high']:,g}{unit} "
        f"({ref['source']})",
        suggested=fmt(ref["high"]) + unit if over else "No change",
        flagged=over,
    )


def assumption_review(result: AnalysisResult) -> list[AssumptionRow]:
    """ "Assumptions to revise": selling price and yield against the sector
    benchmark table, utilisation against the usual ramp-up, cost escalation,
    interest rate and depreciation. A row the DPR gives nothing for says so;
    nothing is estimated."""
    from .financial_ratios import fixed_assets

    fin = _financials(result)
    reference = (fin.benchmark_used or {}).get("reference", {})
    rows: list[AssumptionRow] = []

    price = _range_row("Selling price", reference.get("price"), money=True)
    if price is None:
        ue = fin.unit_economics
        printed = ue.price_per_unit_rupees if ue is not None else None
        price = AssumptionRow(
            assumption="Selling price",
            dpr_value=(
                f"{_rupees(printed.value)} per {ue.unit_name or 'unit'}"
                if printed is not None and printed.status == "extracted"
                else None
            ),
            reference="No benchmark for this sector yet",
            suggested="Support it with buyer or mandi rates",
        )
    rows.append(price)

    rows.append(_utilisation_row(fin))

    rows.append(
        _range_row("Yield / output per unit", reference.get("yield"), money=False)
        or AssumptionRow(
            assumption="Yield / output per unit",
            reference="No benchmark for this sector yet",
        )
    )

    cost_up = _stated_ratio(fin, "cost_escalation_pct")
    price_up = _stated_ratio(fin, "price_escalation_pct")
    inflation = f"RBI inflation target {INFLATION_TARGET_PCT:g}% p.a."
    if cost_up is None:
        rows.append(
            AssumptionRow(
                assumption="Input cost escalation",
                dpr_value="Not stated",
                reference=inflation,
                suggested=(
                    f"State it; at least {price_up:g}% p.a. (your price escalation)"
                    if price_up
                    else "State it in the assumptions"
                ),
                flagged=bool(price_up),
            )
        )
    else:
        behind = price_up is not None and cost_up < price_up
        rows.append(
            AssumptionRow(
                assumption="Input cost escalation",
                dpr_value=f"{cost_up:g}% p.a."
                + (f" (prices {price_up:g}% p.a.)" if price_up is not None else ""),
                reference=inflation,
                suggested=f"{price_up:g}% p.a., as prices" if behind else "No change",
                flagged=behind,
            )
        )

    standard = fin.standard_interest_rate_pct
    effective = fin.effective_interest_rate_pct
    rate = effective or standard
    rows.append(
        AssumptionRow(
            assumption="Interest rate",
            dpr_value=(
                f"{rate:g}%"
                + (
                    f" (after subvention; {standard:g}% before)"
                    if effective and standard and effective != standard
                    else ""
                )
                if rate
                else "Not stated"
            ),
            reference="Your bank's current rate (confirm with the branch)",
            suggested="No change" if rate else "State the rate and its basis",
            flagged=rate is None,
        )
    )

    rows.append(_depreciation_row(fin, fixed_assets(fin)))
    return rows


def _utilisation_row(fin) -> AssumptionRow:
    from .unit_economics import (
        _unit_economics_of,
        _utilisation_by_year,
        full_capacity_per_year,
    )

    norm = " · ".join(f"{v:g}%" for v in UTILISATION_RAMP_UP)
    ue = _unit_economics_of(fin)
    by_year = _utilisation_by_year(ue, full_capacity_per_year(ue, fin)) if ue else {}
    printed = [by_year.get(year) for year in (1, 2, 3)]
    if all(v is None for v in printed):
        return AssumptionRow(
            assumption="Capacity utilisation, Yr 1–3",
            dpr_value="Not stated",
            reference=f"Typical ramp-up {norm}",
            suggested="State year-wise utilisation",
            flagged=True,
        )
    over = any(
        v is not None and v > cap + 0.05
        for v, cap in zip(printed, UTILISATION_RAMP_UP, strict=True)
    )
    return AssumptionRow(
        assumption="Capacity utilisation, Yr 1–3",
        dpr_value=" · ".join("—" if v is None else f"{v:g}%" for v in printed),
        reference=f"Typical ramp-up {norm}",
        suggested=(
            " · ".join(
                "—" if v is None else f"{min(v, cap):g}%"
                for v, cap in zip(printed, UTILISATION_RAMP_UP, strict=True)
            )
            if over
            else "No change"
        ),
        flagged=over,
    )


def _depreciation_row(fin, assets: float | None) -> AssumptionRow:
    method = fin.stated_ratios.depreciation_method if fin.stated_ratios else None
    rate = _stated_ratio(fin, "depreciation_rate_pct")
    first = next((y for y in fin.years if y.year == 1), None)
    implied = (
        round(first.depreciation / assets * 100, 1)
        if first is not None and first.depreciation and assets
        else None
    )
    shown = " ".join(
        x for x in (method, f"{rate:g}%" if rate is not None else None) if x
    )
    if not shown and implied is not None:
        shown = f"{implied:g}% of fixed assets in year 1 (implied)"
    used = rate if rate is not None else implied
    high = used is not None and used > IT_PLANT_WDV_PCT + 0.5
    return AssumptionRow(
        assumption="Depreciation method / rate",
        dpr_value=shown or "Not stated",
        reference=(
            f"Income Tax: plant {IT_PLANT_WDV_PCT:g}% WDV, building 10% WDV; "
            "Companies Act: plant about 6.33% SLM"
        ),
        suggested=(
            f"Use {IT_PLANT_WDV_PCT:g}% WDV (or SLM over the useful life)"
            if high
            else "No change"
            if shown
            else "State the method and rate"
        ),
        flagged=high or not shown,
    )


# --- Documents to prepare ----------------------------------------------------

# (document, checklist item that tells us about it, action when missing).
# None: the analysis does not check it yet -- printed "To confirm".
DOCUMENTS: tuple[tuple[str, str | None, str], ...] = (
    (
        "KYC of applicant and promoters",
        None,
        "Verified by IFPL under our Value Addition service.",
    ),
    (
        "Land documents (title, RTC / 7-12 / patta, lease, EC)",
        "DOC_PREMISES",
        "Attach the title, lease or rent agreement for the project site.",
    ),
    (
        "Entity registration, deed / MoA, Udyam",
        "DOC_REGISTRATION",
        "Keep the registration certificate and Udyam number ready.",
    ),
    (
        "Last 3 years' ITR and financials",
        "DOC_PAST_FINANCIALS",
        "Keep the last three years' returns and statements ready.",
    ),
    (
        "Machinery quotations (≥ 2), civil estimate",
        "DOC_QUOTATION",
        "Attach at least two current quotations matching the project cost.",
    ),
    (
        "Statutory approvals / NOCs",
        "DOC_APPROVALS",
        "List the approvals the project needs and their status.",
    ),
    (
        "Scheme portal registration ID",
        "DOC_SCHEME_REGISTRATION",
        "Register on the scheme portal and note the ID in the DPR.",
    ),
    (
        "Training / category certificates",
        "DOC_CERTIFICATES",
        "Attach any training or category certificate the scheme needs.",
    ),
    (
        "Buyer / buy-back tie-up letters",
        "SEC_DEMAND_EVIDENCE",
        "Attach buyer, union or buy-back letters showing demand.",
    ),
)


def document_checklist(result: AnalysisResult) -> list[DocumentStatus]:
    answers = checklist_answers(result)
    rows = []
    for document, check_id, action in DOCUMENTS:
        answer = answers.get(check_id) if check_id else None
        status = {"ok": "available", "issue": "missing"}.get(answer, "to_confirm")
        if answer == "not_applicable":
            action = "The DPR suggests it is not needed; confirm with your bank."
        rows.append(
            DocumentStatus(
                document=document,
                status=status,
                action=None if status == "available" else action,
            )
        )
    return rows


# --- Scheme fit ----------------------------------------------------------------


def _claimed_scheme(result: AnalysisResult) -> str | None:
    return next(
        (
            f.value
            for out in result.agent_outputs.values()
            for f in out.document_facts
            if f.field == "scheme_claimed"
        ),
        None,
    )


def _column(rule, record: dict | None) -> SchemeColumn:
    most = record.get("max_subsidy_lakh") if record else None
    subsidy = None
    if rule.subsidy_pct or most is not None:
        subsidy = " · ".join(
            x
            for x in (
                f"{rule.subsidy_pct:g}%" if rule.subsidy_pct else None,
                f"₹ {most:g} L" if most is not None else None,
            )
            if x
        )
    elif rule.key in ("aif", "cgtmse", "nabard_refinance"):
        subsidy = "None"
    subvention = (
        f"{rule.subvention_pct:g}% for {rule.subvention_years} yrs"
        if rule.subvention_pct
        else None
    )
    return SchemeColumn(
        name=rule.name,
        eligible="partly" if record and record.get("gaps") else "likely",
        cost_ceiling=f"₹ {rule.project_cost_cap_lakh:g} L"
        if rule.project_cost_cap_lakh
        else None,
        contribution=f"{rule.own_contribution_min_pct:g}%"
        if rule.own_contribution_min_pct
        else None,
        capital_subsidy=subsidy,
        interest_subvention=subvention,
        credit_guarantee=rule.credit_guarantee,
        total_benefit=f"₹ {most:g} lakh" if most is not None else None,
    )


def _lakh(value: float) -> str:
    return f"₹ {value:,.2f}".rstrip("0").rstrip(".") + " L"


def _option_column(option, sector, gaps: list) -> SchemeColumn:
    """A comparison column for one scheme or a combination
    (schemes.SchemeOption). Benefits are the most the scheme can give this
    project, from the scheme table: an estimate, never a sanction."""
    from .schemes import NO_SUBSIDY, SCHEME_RULES, covers, subsidy_pct

    rules = option.rules
    if any(covers(r, sector) is False for r in rules):
        eligible = "no"
    elif gaps:
        eligible = "partly"
    else:
        eligible = "likely"
    caps = [r.project_cost_cap_lakh for r in rules if r.project_cost_cap_lakh]
    minimum = [r.own_contribution_min_pct for r in rules if r.own_contribution_min_pct]
    giver = next((r for r in rules if r.subsidy_pct and r.key not in NO_SUBSIDY), None)
    if option.subsidy_lakh is not None:
        subsidy = " · ".join(
            x
            for x in (
                f"{subsidy_pct(giver, True):g}%" if giver else None,
                _lakh(option.subsidy_lakh),
            )
            if x
        )
        notes = []
        if "structure" in (option.subsidy_how or ""):
            notes.append("structure only")
        if giver and subsidy_pct(giver, True) != giver.subsidy_pct:
            notes.append(f"up to {giver.subsidy_pct:g}%")
        if notes:
            subsidy += f" ({'; '.join(notes)})"
    elif all(r.key in NO_SUBSIDY for r in rules):
        subsidy = "None"
    else:
        subsidy = f"{subsidy_pct(giver, True):g}% (cost not stated)" if giver else None
    subvention = "; ".join(
        f"{r.subvention_pct:g}% for {r.subvention_years} yrs"
        for r in rules
        if r.subvention_pct
    )
    if subvention and option.subvention_lakh:
        subvention += f" (≈ {_lakh(option.subvention_lakh)})"
    by_key = {r.key: r for r in SCHEME_RULES}
    inside = option.keys
    combinable = sorted(
        {
            by_key[k].name
            for r in rules
            for k in r.combines_with
            if k not in inside and covers(by_key[k], sector) is not False
        }
    )
    return SchemeColumn(
        name=option.name,
        eligible=eligible,
        cost_ceiling=_lakh(min(caps)) if caps else "No ceiling",
        contribution=f"{max(minimum):g}%" if minimum else None,
        capital_subsidy=subsidy,
        interest_subvention=subvention or "None",
        credit_guarantee="; ".join(
            r.credit_guarantee for r in rules if r.credit_guarantee
        )
        or None,
        tenure_moratorium="; ".join(
            r.tenure_moratorium for r in rules if r.tenure_moratorium
        )
        or None,
        combinable=", ".join(combinable) or "—",
        total_benefit=(
            f"₹ {option.total:,.2f}".rstrip("0").rstrip(".") + " lakh"
            if eligible != "no" and option.total
            else None
        ),
    )


def _option_gaps(
    option, fin, claimed: str, kept=frozenset(), record_gaps=()
) -> list[SchemeGap]:
    """Gaps under the recommended option: the schemes to add to the DPR,
    the gaps already found for the claimed schemes it keeps (`kept`,
    `record_gaps`), and for each added scheme its project-cost ceiling and
    minimum own contribution."""
    from .financial_ratios import project_cost
    from .schemes import own_contribution

    added = [r for r in option.rules if r.key not in kept]
    names = " + ".join(r.name for r in added)
    gaps = [
        SchemeGap(
            criterion="Scheme named in the DPR",
            requires=option.name,
            dpr_shows=claimed[:60],
            action=(
                f"Add {names} to the DPR's means of finance and apply on its portal."
                if kept
                else f"Apply under {names}; register on its portal and name it "
                "in the DPR's means of finance."
            ),
        )
    ]
    gaps.extend(record_gaps)
    cost, own = project_cost(fin), own_contribution(fin)
    for rule in added:
        if rule.project_cost_cap_lakh and cost and cost > rule.project_cost_cap_lakh:
            gaps.append(
                SchemeGap(
                    criterion="Project cost ceiling",
                    requires=f"Up to {rule.project_cost_cap_lakh:g} lakh",
                    dpr_shows=f"{cost:g} lakh",
                    action="The benefit is computed on the capped cost.",
                )
            )
        if rule.own_contribution_min_pct and cost and own is not None:
            share = own / cost * 100
            if share < rule.own_contribution_min_pct * 0.99:
                gaps.append(
                    SchemeGap(
                        criterion="Promoter contribution",
                        requires=f"At least {rule.own_contribution_min_pct:g}%",
                        dpr_shows=f"{share:.1f}%",
                        action="Raise the contribution to the minimum.",
                    )
                )
    return gaps


# Within this much (lakh, or this share) the chosen scheme is as good as
# the best: the estimate is not that precise.
SCHEME_BENEFIT_MARGIN_LAKH = 0.5
SCHEME_BENEFIT_MARGIN_PCT = 5.0


def scheme_review(result: AnalysisResult) -> SchemeReview | None:
    """The scheme the DPR names, against the schemes the table says cover
    this kind of project (schemes.scheme_options): the most each can give,
    the best one, and the gaps under it. Only harvester, polyhouse, cold
    storage and dairy-farm projects are compared; for the rest the chosen
    scheme's rules are checked and the comparison is "not assessed"."""
    from .schemes import (
        SECTOR_LABEL,
        option_for,
        project_sector,
        scheme_options,
        schemes_claimed,
    )

    fin = _financials(result)
    sector = project_sector(fin) if result.extracted_financials else None
    claimed = _claimed_scheme(result)
    options = scheme_options(fin, sector)
    if not claimed:
        if options:
            best = options[0]
            return SchemeReview(
                recommended=_option_column(best, sector, []),
                alternative=_option_column(options[1], sector, [])
                if len(options) > 1
                else None,
                recommendation=(
                    f"Your DPR does not name a scheme. {best.name} gives this "
                    f"project the most of the schemes we compared (about "
                    f"₹ {best.total:g} lakh); confirm it with your bank."
                ),
                gaps=_option_gaps(best, fin, "No scheme named"),
            )
        return SchemeReview(
            recommendation=(
                "Your DPR does not name a scheme. Ask your bank which scheme "
                "applies before you submit."
            )
        )
    rules = schemes_claimed(claimed)
    records = {r["scheme"]: r for r in result.scheme_checks}
    if not rules:
        return SchemeReview(
            chosen=SchemeColumn(name=claimed[:60]),
            recommended=_option_column(options[0], sector, []) if options else None,
            recommendation=(
                f"{claimed[:60]} is not in our scheme table yet, so its rules "
                "were not checked. Confirm its conditions with your bank."
            ),
        )
    record_gaps = [
        SchemeGap(**g) for r in result.scheme_checks for g in r.get("gaps", [])
    ]
    if not options:
        # Not a sector the table compares: the chosen scheme's own rules.
        lead = next((r for r in rules if r.subsidy_pct), rules[0])
        names = ", ".join(r.name for r in rules)
        text = f"Your DPR claims {names}. " + (
            f"{len(record_gaps)} of its rules are not met yet; fix them below."
            if record_gaps
            else "No rule we check is broken; the bank confirms eligibility."
        )
        if sector == "dairy_farm":
            text += (
                " No central scheme in our table gives a capital subsidy for a "
                "dairy farm: NABARD's DEDS subsidy ended in 2020-21, and AHIDF "
                "covers dairy processing, not milch animals. Ask your bank "
                "about your state's animal husbandry schemes."
            )
        return SchemeReview(
            chosen=_column(lead, records.get(lead.name)),
            verdict="not_eligible"
            if sector == "dairy_farm" and any(r.sectors for r in rules)
            else "not_assessed",
            gaps=record_gaps,
            recommendation=text,
        )

    chosen = option_for(rules, fin)
    chosen_col = _option_column(chosen, sector, record_gaps)
    covered = chosen_col.eligible != "no"
    usable = [o for o in options if o.keys != chosen.keys]
    best = options[0]
    margin = max(
        SCHEME_BENEFIT_MARGIN_LAKH, chosen.total * SCHEME_BENEFIT_MARGIN_PCT / 100
    )
    if not covered:
        verdict = "not_eligible"
    elif best.keys == chosen.keys or best.total <= chosen.total + margin:
        verdict = "correct"
    else:
        verdict = "better_available"
    what = SECTOR_LABEL.get(sector, sector)

    if verdict == "correct":
        alternative = usable[0] if usable else None
        recommended_col = chosen_col
        gaps = record_gaps
        text = (
            f"Stay with {chosen.name}. Of the schemes we compared for {what}, "
            f"it gives the most (about ₹ {chosen.total:g} lakh)."
            + (
                f" {len(gaps)} of its rules are not met yet; fix them below."
                if gaps
                else ""
            )
        )
    else:
        alternative = next((o for o in usable if o.keys != best.keys), None)
        kept = chosen.keys & best.keys
        gaps = _option_gaps(
            best,
            fin,
            claimed,
            kept=kept,
            record_gaps=[
                SchemeGap(**g)
                for r in result.scheme_checks
                if r["scheme"] in {x.name for x in best.rules if x.key in kept}
                for g in r.get("gaps", [])
            ],
        )
        recommended_col = _option_column(best, sector, gaps)
        # The scheme's name is a gap only until the DPR is changed; any other
        # gap makes it a partial fit.
        recommended_col = recommended_col.model_copy(
            update={"eligible": "partly" if len(gaps) > 1 else "likely"}
        )
        if best.keys > chosen.keys:
            extra = " + ".join(r.name for r in best.rules if r.key not in chosen.keys)
            move = f"Keep {chosen.name} and add {extra}"
        else:
            move = f"Switch to {best.name}"
        text = (
            f"{move}: about ₹ {best.total:g} lakh for {what}, against "
            + (
                f"₹ {chosen.total:g} lakh under {chosen.name}."
                if covered
                else f"nothing under {chosen.name}, which does not cover it."
            )
            + " Confirm with your bank before you change the DPR."
        )
    return SchemeReview(
        chosen=chosen_col,
        recommended=recommended_col,
        alternative=_option_column(alternative, sector, []) if alternative else None,
        verdict=verdict,
        recommendation=text,
        gaps=gaps,
    )


# --- Stress test -----------------------------------------------------------------


def stress_test(result: AnalysisResult) -> StressTest | None:
    """The stress box: the average and lowest DSCR with the selling price
    10% lower (every cost unchanged), and which assumption has the least
    headroom before the average DSCR reaches 1.10."""
    from .financial_ratios import STRESS_DSCR_FLOOR, headroom

    lowest = _value(result, "dscr_stress_price_10")
    average = _value(result, "dscr_average_price_10")
    if lowest is None and average is None:
        return None
    room = headroom(_financials(result))
    sensitive = None
    if room and room.get("price_fall_pct") is not None:
        price, cost = room["price_fall_pct"], room.get("cost_rise_pct")
        if price == 0:
            sensitive = (
                f"selling price -- the average DSCR is below {STRESS_DSCR_FLOOR:.2f} "
                "even before any fall"
            )
        elif cost is None or price <= cost:
            sensitive = (
                f"selling price (a {price:g}% fall takes the average DSCR to "
                f"{STRESS_DSCR_FLOOR:.2f}"
                + (f"; costs can rise {cost:g}%)" if cost is not None else ")")
            )
        else:
            sensitive = (
                f"operating costs (a {cost:g}% rise takes the average DSCR to "
                f"{STRESS_DSCR_FLOOR:.2f}; the price can fall {price:g}%)"
            )
    return StressTest(
        price_fall_pct=10,
        average_dscr=Decimal(str(average)) if average is not None else None,
        minimum_dscr=Decimal(str(lowest)) if lowest is not None else None,
        minimum_year=_calc(result, "dscr_stress_price_10").get("note")
        if lowest is not None
        else None,
        most_sensitive=sensitive,
    )
