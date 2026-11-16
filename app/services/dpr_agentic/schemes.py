"""Scheme rules from a fixed table, checked in code: does the DPR's claim fit
the scheme it names?

The policy agent (grounding.py) searches each scheme's conditions and turns
official statements into "confirm with the bank" questions -- evidence, not
a check. This module does the arithmetic a credit officer does first: the
most a scheme can give for this project cost, compared with the subsidy the
means of finance counts; the scheme's project-cost cap; its minimum own
contribution; and AIF's interest subvention. It never declares a borrower
eligible: a pass means only "no rule in this table is broken".

Rules are deliberately the LENIENT end of each scheme (e.g. PMEGP's
special-category rural rate, 35%, since the applicant's category and the
area are rarely stated), so a flag means the claim exceeds even the best
case. Findings are should-fix with a check_id, so the verifier can neither
drop nor downgrade them.

Approved for use 2026-11-13 by the LAS project team, provisional until
credit reviews them (benchmarks.py, same arrangement). Sources (searched
2026-11-13):
  PMEGP: project cost up to Rs 50 lakh (manufacturing) / Rs 20 lakh
    (service); subsidy 15% urban / 25% rural (general), 25% / 35% (special
    category); own contribution 10% (general) / 5% (special). KVIC revised
    guidelines via dkvib.delhi.gov.in, sbi.co.in, myscheme.gov.in.
  PMFME: 35% credit-linked capital subsidy, at most Rs 10 lakh per unit;
    own contribution at least 10%. canarabank.com, apmsmeone.ap.gov.in,
    cuttack.odisha.gov.in.
  SMAM, custom hiring centres: 40% of project cost, projects up to Rs 250
    lakh (subsidy at most Rs 100 lakh); state-operated, rates vary.
    vikaspedia.in cost norms.
  AIF: 3% a year interest subvention on loans up to Rs 2 crore, for at most
    7 years; CGTMSE fee paid by the government; no capital subsidy.
    vikaspedia.in, hdfc.bank.in, the 2023 guidelines (gobardhan.sbm.gov.in).
  MIDH / NHB cold storage: 35% (general) / 50% (hilly, scheduled areas) of
    the admissible cost, the cost norm per tonne, up to 5,000 MT
    (benchmarks.COLD_STORAGE_TYPES). nhb.gov.in, Bihar model project.
  CGTMSE, NABARD refinance: a guarantee and a refinance line; no capital
    subsidy.
  Added for the scheme comparison (searched 2026-11-13):
  AIF: eligible activities include custom hiring centres (at least 4
    implements) and cold stores; polyhouses are named in a Parliament reply
    (rsdebate.nic.in, 2024) -- confirm with the bank. In convergence with
    State and Central schemes (PMEGP, NHB, MIDH...): hdfc.bank.in.
    Moratorium 6-24 months; subvention up to 7 years.
  MIDH protected cultivation: up to 50% of the cost norm (Rs 844-1,650 a
    m2 by type) on at most 4,000 m2 per beneficiary; credit-linked,
    back-ended (Lok Sabha answer, eparlib.nic.in).
  AHIDF: dairy and meat processing, feed plants and allied infrastructure,
    not milch animals; 3% interest subvention for up to 8 years; loan up
    to 90% of cost; repayment up to 10 years including up to 2 years'
    moratorium; credit guarantee on 25% of the loan for MSMEs (dahd.gov.in
    FAQ, sbi.co.in, iob.in, nabard.org CGS-AHD).
  Dairy farms (milch animals): NABARD's DEDS capital subsidy was
    discontinued from 2020-21 (vikaspedia.in and bank pages), so no
    central scheme in this table gives one.

Pure functions; no LLM, no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .financial_schema import ProjectFinancials
from .finding_rules import compute_dedup_key
from .schemas import DocumentFact, Finding

_REVIEWED_BY = "LAS project team (provisional, pending credit review)"
_REVIEWED_ON = "2026-11-13"
# A claim within this share above the scheme maximum is rounding.
TOLERANCE = 0.01


@dataclass(frozen=True)
class SchemeRule:
    key: str
    name: str
    pattern: re.Pattern
    subsidy_pct: float | None = None  # of the eligible project cost
    subsidy_cap_lakh: float | None = None
    project_cost_cap_lakh: float | None = None  # subsidy only up to this cost
    own_contribution_min_pct: float | None = None
    subvention_pct: float | None = None
    subvention_loan_cap_lakh: float | None = None
    subvention_years: int | None = None
    credit_guarantee: str | None = None
    # Project sectors the scheme covers (project_sector()); None: any, or
    # not known to this table -- never judged.
    sectors: frozenset[str] | None = None
    tenure_moratorium: str | None = None
    # Scheme keys it can be combined with for the same project.
    combines_with: tuple[str, ...] = ()
    # What a recommendation promises: the CAUTIOUS end (general category,
    # general area, one machine), where the checks above use the lenient
    # end. A recommendation must not count on the best case.
    cautious_pct: float | None = None
    cautious_cap_lakh: float | None = None
    basis: str = ""
    status: str = "approved"
    reviewed_by: str | None = _REVIEWED_BY
    reviewed_on: str | None = _REVIEWED_ON


SCHEME_RULES: tuple[SchemeRule, ...] = (
    SchemeRule(
        key="pmegp",
        name="PMEGP",
        pattern=re.compile(
            r"\bpmegp\b|prime minister.{0,20}employment generation", re.I
        ),
        subsidy_pct=35,
        project_cost_cap_lakh=50,
        own_contribution_min_pct=5,
        cautious_pct=15,
        credit_guarantee="CGTMSE",
        combines_with=("aif",),
        basis=(
            "KVIC guidelines: project cost up to Rs 50 lakh (manufacturing) or "
            "Rs 20 lakh (service); subsidy 15-35% by category and area (35% "
            "used, the highest); own contribution at least 5% (special "
            "category) or 10% (general)."
        ),
    ),
    SchemeRule(
        key="pmfme",
        name="PMFME",
        pattern=re.compile(r"\bpmfme\b|formali[sz]ation of micro food", re.I),
        subsidy_pct=35,
        subsidy_cap_lakh=10,
        own_contribution_min_pct=10,
        basis=(
            "Scheme guidelines: 35% credit-linked capital subsidy, at most "
            "Rs 10 lakh per unit; own contribution at least 10%."
        ),
    ),
    SchemeRule(
        key="smam",
        name="SMAM",
        pattern=re.compile(r"\bsmam\b|sub[- ]?mission on agri\w* mechani", re.I),
        subsidy_pct=40,
        subsidy_cap_lakh=100,
        project_cost_cap_lakh=250,
        # One self-propelled combine, "other beneficiary": 40%, at most
        # Rs 6.40 lakh (50% / Rs 8 lakh for priority categories). SMAM cost
        # norms, vikaspedia.in; krishakjagat.org.
        cautious_cap_lakh=6.4,
        sectors=frozenset({"harvester"}),
        combines_with=("aif",),
        basis=(
            "SMAM cost norms: 40% of project cost for custom hiring centres, "
            "projects up to Rs 250 lakh, subsidy at most Rs 100 lakh; one "
            "self-propelled combine for an individual: 40%, at most Rs 6.40 "
            "lakh (50%, Rs 8 lakh for priority categories); state-operated, "
            "so state rates may be lower."
        ),
    ),
    SchemeRule(
        key="aif",
        name="Agriculture Infrastructure Fund (AIF)",
        pattern=re.compile(r"\baif\b|agri(?:culture)?\.?\s*infra", re.I),
        subvention_pct=3,
        subvention_loan_cap_lakh=200,
        subvention_years=7,
        credit_guarantee="CGTMSE, fee paid by the government",
        sectors=frozenset({"harvester", "polyhouse", "cold_storage"}),
        tenure_moratorium="Subvention up to 7 yrs · 6–24 mo",
        combines_with=("pmegp", "smam", "midh"),
        basis=(
            "AIF guidelines: 3% a year interest subvention on loans up to "
            "Rs 2 crore, for at most 7 years; CGTMSE fee paid by the "
            "government; no capital subsidy."
        ),
    ),
    SchemeRule(
        key="midh",
        name="MIDH / NHB",
        pattern=re.compile(
            r"\bmidh\b|\bnhb\b|national horticulture board|"
            r"integrated development of horticulture",
            re.I,
        ),
        subsidy_pct=50,
        cautious_pct=35,
        sectors=frozenset({"polyhouse", "cold_storage"}),
        combines_with=("aif",),
        basis=(
            "MIDH/NHB: credit-linked back-ended subsidy of 35% (general) or "
            "50% (hilly and scheduled areas) of the admissible cost, computed "
            "on the cost norm (cold storage: per tonne, up to 5,000 MT; "
            "polyhouse: 50% of Rs 844-1,650 a m2, up to 4,000 m2)."
        ),
    ),
    SchemeRule(
        key="ahidf",
        name="AHIDF",
        pattern=re.compile(
            r"\bahidf\b|animal husbandry infrastructure development", re.I
        ),
        own_contribution_min_pct=10,
        subvention_pct=3,
        subvention_years=8,
        credit_guarantee="25% of the loan (MSMEs)",
        sectors=frozenset({"dairy_processing"}),
        tenure_moratorium="Up to 10 yrs · up to 24 mo",
        basis=(
            "AHIDF: dairy and meat processing, feed plants and allied "
            "infrastructure (not milch animals); 3% interest subvention for "
            "up to 8 years; loan up to 90% of cost; repayment up to 10 years "
            "with up to 2 years' moratorium; 25% credit guarantee for MSMEs."
        ),
    ),
    SchemeRule(
        key="cgtmse",
        name="CGTMSE",
        pattern=re.compile(r"cgtmse|credit guarantee fund trust", re.I),
        basis="A credit guarantee for the lender; no capital subsidy.",
    ),
    SchemeRule(
        key="nabard_refinance",
        name="NABARD refinance",
        pattern=re.compile(r"nabard\s+refinance", re.I),
        basis="A refinance line for the lender; no capital subsidy.",
    ),
)
# Schemes that give no capital subsidy at all.
NO_SUBSIDY = frozenset({"aif", "ahidf", "cgtmse", "nabard_refinance"})
# MIDH protected cultivation: assistance on at most this area per beneficiary.
MIDH_POLYHOUSE_MAX_SQ_M = 4000
MIDH_MAX_PCT = 50  # hilly / scheduled areas: the lenient end
COLD_STORAGE_MAX_MT = 5000

# A funding line is subsidy only when it says so. "Margin money" alone is
# the borrower's own share in bank usage (seen live: "APPLICANT MARGIN
# MONEY" 7.99 lakh = 25% own contribution); PMEGP's subsidy is printed as
# "margin money subsidy", which "subsid" matches.
_SUBSIDY_LINE = re.compile(r"subsid|grant|back[\s\-]*ended", re.I)
_OWN_LINE = re.compile(
    r"promoter|own\s+(?:contribution|fund|capital|share)|equity|owner|"
    r"applicant|beneficiary|margin",
    re.I,
)


SECTOR_LABEL = {
    "harvester": "a custom hiring centre (harvester)",
    "polyhouse": "protected cultivation (polyhouse)",
    "cold_storage": "a cold storage",
    "dairy_farm": "a dairy farm (milch animals)",
    "dairy_processing": "dairy or meat processing and feed plants",
}


def schemes_claimed(text: str | None) -> list[SchemeRule]:
    if not text:
        return []
    return [rule for rule in SCHEME_RULES if rule.pattern.search(text)]


def _sum(fin: ProjectFinancials, pattern: re.Pattern, exclude=None) -> float | None:
    lines = [
        c.amount
        for c in fin.means_of_finance_components
        if pattern.search(c.label) and not (exclude and exclude.search(c.label))
    ]
    return round(sum(lines), 2) if lines else None


def subsidy_claimed(fin: ProjectFinancials) -> float | None:
    return _sum(fin, _SUBSIDY_LINE)


def own_contribution(fin: ProjectFinancials) -> float | None:
    return _sum(fin, _OWN_LINE, exclude=_SUBSIDY_LINE)


def _midh_polyhouse_max(
    fin: ProjectFinancials, pct: float = MIDH_MAX_PCT, *, cautious: bool = False
) -> tuple[float, str] | None:
    """Checking a claim (lenient): MIDH also assists planting material and
    cultivation, which this table has no norms for, so 50% of the whole
    project cost. Recommending (cautious): `pct` of the structure only, at
    most `pct` of the cost norm (Rs 1,650 a m2 when the type is not known)
    on at most 4,000 m2."""
    from .benchmarks import _structure_of

    if not cautious:
        cost = fin.cost_of_project_stated_total
        if not cost:
            return None
        return round(pct / 100 * cost, 2), (
            f"{pct:g}% of the {cost:g} lakh project (structure, planting "
            "material and cultivation)"
        )
    ph = fin.polyhouse_assumptions
    area = ph.area_sq_m
    if area.status != "extracted" or not area.value:
        return None
    norm = _structure_of(fin, []).cost_per_sq_m_rupees.high
    covered = min(area.value, MIDH_POLYHOUSE_MAX_SQ_M)
    value = pct / 100 * norm * covered / 100_000
    how = f"{pct:g}% of {norm:,.0f} rupees a m2 x {covered:g} m2"
    cost = ph.structure_cost_lakh
    if cost.status == "extracted" and cost.value:
        actual = pct / 100 * cost.value
        if actual < value:
            value, how = actual, f"{pct:g}% of the {cost.value:g} lakh structure"
    return round(value, 2), how


def _midh_max(
    fin: ProjectFinancials, pct: float = MIDH_MAX_PCT, *, cautious: bool = False
) -> tuple[float, str] | None:
    """The most MIDH/NHB can give: `pct` (50% at most) of the cost norm x
    capacity, for a cold storage or a polyhouse (the MIDH cases with a
    code-checkable norm here)."""
    from .benchmarks import _storage_type_of, is_cold_storage, is_polyhouse

    if is_polyhouse(fin) and not is_cold_storage(fin):
        return _midh_polyhouse_max(fin, pct, cautious=cautious)
    if not is_cold_storage(fin):
        return None
    capacity = fin.cold_storage_assumptions.capacity_mt
    if capacity.status != "extracted" or not capacity.value:
        return None
    norm, _ = _storage_type_of(fin, [])
    tonnes = min(capacity.value, COLD_STORAGE_MAX_MT)
    value = pct / 100 * norm.cost_per_mt_rupees.high * tonnes / 100_000
    return round(value, 2), (
        f"{pct:g}% of {norm.cost_per_mt_rupees.high:,.0f} rupees a tonne "
        f"x {tonnes:g} tonnes"
    )


def subsidy_pct(rule: SchemeRule, cautious: bool = False) -> float | None:
    if cautious and rule.cautious_pct is not None:
        return rule.cautious_pct
    return rule.subsidy_pct


def max_subsidy(
    rule: SchemeRule, fin: ProjectFinancials, *, cautious: bool = False
) -> tuple[float, str] | None:
    """(most the scheme can give in lakh, how it was computed), or None
    when the table has no subsidy arithmetic for it. `cautious`: the
    figure a recommendation may promise (SchemeRule.cautious_*)."""
    pct = subsidy_pct(rule, cautious)
    if rule.key == "midh":
        return _midh_max(fin, pct, cautious=cautious)
    cost = fin.cost_of_project_stated_total
    if pct is None or not cost:
        return None
    eligible_cost = min(cost, rule.project_cost_cap_lakh or cost)
    value = pct / 100 * eligible_cost
    how = f"{pct:g}% of {eligible_cost:g} lakh"
    caps = [rule.subsidy_cap_lakh]
    if cautious:
        caps.append(rule.cautious_cap_lakh)
    cap = min((c for c in caps if c is not None), default=None)
    if cap is not None and value > cap:
        value, how = cap, f"{pct:g}%, capped at {cap:g} lakh"
    return round(value, 2), how


def project_sector(fin: ProjectFinancials | None) -> str | None:
    """The one sector the scheme table can judge, from the sector groups
    extracted: harvester, polyhouse, cold_storage, or dairy_farm (a milch
    herd). None for any other project, or one that looks like two."""
    from .benchmarks import is_cold_storage, is_dairy, is_harvester, is_polyhouse

    found = []
    if is_harvester(fin):
        found.append("harvester")
    if is_polyhouse(fin):
        found.append("polyhouse")
    if is_cold_storage(fin):
        found.append("cold_storage")
    if is_dairy(fin) and fin.dairy_assumptions.animals.status == "extracted":
        found.append("dairy_farm")
    return found[0] if len(found) == 1 else None


def covers(rule: SchemeRule, sector: str | None) -> bool | None:
    """True/False when the table knows whether the scheme covers this
    sector; None when it cannot say."""
    if rule.sectors is None or sector is None:
        return None
    return sector in rule.sectors


def subvention_value(
    rule: SchemeRule, fin: ProjectFinancials
) -> tuple[float, str] | None:
    """Interest saved by the scheme's subvention: its rate on the loan
    outstanding at the start of each year, for its years, on the loan up to
    its cap. Outstanding follows the DPR's own principal by year; without
    it, equal principal over the tenor (else the subvention years) after
    the moratorium."""
    from .financial_calc import _debt_service_for_year
    from .financial_ratios import term_loan

    loan = term_loan(fin)
    if not rule.subvention_pct or not rule.subvention_years or not loan:
        return None
    covered = min(loan, rule.subvention_loan_cap_lakh or loan)
    principal = {}
    for y in sorted(fin.years, key=lambda y: y.year):
        paid, interest = _debt_service_for_year(fin, y)
        if paid is None and y.total_instalment is not None:
            add = y.interest_on_term_loan or interest
            paid = y.total_instalment - add if add is not None else None
        if paid is None:
            break
        principal[y.year] = paid
    if len(principal) < rule.subvention_years:
        tenor = fin.loan_tenor_years
        years = (
            int(tenor.value)
            if tenor.status == "extracted" and tenor.value
            else (rule.subvention_years)
        )
        holiday = fin.loan_moratorium_years
        rest = (
            int(holiday.value) if holiday.status == "extracted" and holiday.value else 0
        )
        repaying = max(years - rest, 1)
        principal = {
            n: 0.0 if n <= rest else loan / repaying for n in range(1, years + 1)
        }
    outstanding, total = loan, 0.0
    for year in range(1, rule.subvention_years + 1):
        if outstanding <= 0:
            break
        total += rule.subvention_pct / 100 * outstanding * covered / loan
        outstanding -= principal.get(year, 0.0)
    return round(total, 2), (
        f"{rule.subvention_pct:g}% a year on the outstanding loan"
        + (f" (up to {covered:g} lakh)" if covered < loan else "")
        + f", {rule.subvention_years} years"
    )


@dataclass(frozen=True)
class SchemeOption:
    """One scheme, or two that combine, with the most they can give this
    project: the comparison's columns."""

    rules: tuple[SchemeRule, ...]
    subsidy_lakh: float | None
    subsidy_how: str | None
    subvention_lakh: float | None
    subvention_how: str | None

    @property
    def keys(self) -> frozenset[str]:
        return frozenset(r.key for r in self.rules)

    @property
    def name(self) -> str:
        return " + ".join(r.name for r in self.rules)

    @property
    def total(self) -> float:
        return round((self.subsidy_lakh or 0) + (self.subvention_lakh or 0), 2)


def option_for(rules: list[SchemeRule] | tuple[SchemeRule, ...], fin) -> SchemeOption:
    """The benefit of a set of schemes: the best capital subsidy among them
    (schemes rarely allow two on one asset) plus every subvention."""
    subsidies = [
        (m, r) for r in rules if (m := max_subsidy(r, fin, cautious=True)) is not None
    ]
    best = max(subsidies, key=lambda x: x[0][0], default=None)
    subventions = [v for r in rules if (v := subvention_value(r, fin)) is not None]
    return SchemeOption(
        rules=tuple(rules),
        subsidy_lakh=best[0][0] if best else None,
        subsidy_how=best[0][1] if best else None,
        subvention_lakh=round(sum(v[0] for v in subventions), 2)
        if subventions
        else None,
        subvention_how="; ".join(v[1] for v in subventions) or None,
    )


def scheme_options(fin: ProjectFinancials, sector: str | None) -> list[SchemeOption]:
    """Every scheme in the table that covers this sector, alone and paired
    with each scheme it combines with, most benefit first. Empty when the
    sector is unknown or no scheme here covers it."""
    if sector is None:
        return []
    fits = [r for r in SCHEME_RULES if covers(r, sector)]
    options = [option_for([r], fin) for r in fits]
    for i, a in enumerate(fits):
        for b in fits[i + 1 :]:
            if b.key in a.combines_with or a.key in b.combines_with:
                # Subsidy-giving scheme first in the name.
                pair = sorted((a, b), key=lambda r: r.key in NO_SUBSIDY)
                options.append(option_for(pair, fin))
    return sorted(options, key=lambda o: (-o.total, len(o.rules)))


def _finding(check_id, category, title, explanation, action, rules) -> Finding:
    f = Finding(
        category=category,
        severity="medium",
        title=title,
        explanation=(
            explanation
            + " Scheme rules: LAS scheme table ("
            + "; ".join(f"{r.name}: {r.basis}" for r in rules)
            + f" Approved {rules[0].reviewed_on}, provisional)."
        ),
        suggested_action=action,
        provenance="calculated",
        verification="source_verified",
        check_id=check_id,
    )
    f.priority = "should_fix"
    f.priority_rule = (
        "SHOULD_FIX: the DPR's claim exceeds the scheme table's rule "
        "(schemes.py); scheme fit is confirmed by the bank, so capped at "
        "should-fix."
    )
    f.finding_class = "document_defect"
    f.dedup_key = compute_dedup_key(f)
    return f


def run_scheme_checks(
    facts: list[DocumentFact], fin: ProjectFinancials | None
) -> tuple[list[Finding], list[dict]]:
    """(findings, records) for the schemes the DPR claims. Records feed
    Annexure F: what was checked, and the most the scheme can give."""
    claimed_text = next((f.value for f in facts if f.field == "scheme_claimed"), None)
    rules = schemes_claimed(claimed_text)
    if not rules or fin is None:
        return [], []
    findings: list[Finding] = []
    records: list[dict] = []
    cost = fin.cost_of_project_stated_total
    subsidy = subsidy_claimed(fin)
    own = own_contribution(fin)

    maxima = {r.key: max_subsidy(r, fin) for r in rules}
    for rule in rules:
        m = maxima[rule.key]
        records.append(
            {
                "scheme": rule.name,
                "max_subsidy_lakh": m[0] if m else None,
                "how": m[1] if m else None,
                "basis": rule.basis,
                "status": f"{rule.status} {rule.reviewed_on}",
                # Each broken rule as an eligibility gap (the 2.x layout's
                # "Eligibility gaps" table): criterion, what the scheme
                # requires, what the DPR shows, what to do.
                "gaps": [],
            }
        )
    by_name = {r["scheme"]: r for r in records}

    def gap(rule, criterion, requires, shows, action):
        # Always called right after the finding it describes: that finding
        # prints the same two figures in the action plan.
        if findings:
            f = findings[-1]
            f.dpr_stated_value = f.dpr_stated_value or shows
            f.should_be = f.should_be or requires
            f.why_it_matters = (
                f.why_it_matters or "The bank checks the claim against scheme rules"
            )
        by_name[rule.name]["gaps"].append(
            {
                "criterion": criterion,
                "requires": requires,
                "dpr_shows": shows,
                "action": action,
            }
        )

    # Subsidy claimed vs the most any claimed scheme gives (lenient: the
    # best scheme, not their sum -- most schemes forbid double subsidy).
    if subsidy:
        givers = [r for r in rules if r.key not in NO_SUBSIDY]
        if not givers:
            findings.append(
                _finding(
                    "SCHEME_NO_SUBSIDY",
                    "funding",
                    "Subsidy counted under a scheme that gives none",
                    f"The means of finance counts {subsidy:g} lakh of subsidy, but "
                    f"the scheme claimed ({', '.join(r.name for r in rules)}) gives "
                    "no capital subsidy.",
                    "Remove the subsidy from the means of finance, or name the "
                    "scheme that gives it.",
                    rules,
                )
            )
            gap(
                rules[0],
                "Capital subsidy",
                "None under this scheme",
                f"{subsidy:g} lakh counted",
                "Remove it, or name the scheme that gives it.",
            )
        else:
            known = [(r, maxima[r.key]) for r in givers if maxima[r.key]]
            if known:
                best_rule, (best, how) = max(known, key=lambda x: x[1][0])
                if subsidy > best * (1 + TOLERANCE):
                    findings.append(
                        _finding(
                            "SCHEME_SUBSIDY_ABOVE_MAX",
                            "funding",
                            "Subsidy claimed above what the scheme gives",
                            f"The means of finance counts {subsidy:g} lakh of "
                            f"subsidy; {best_rule.name} gives at most {best:g} "
                            f"lakh for this project ({how}).",
                            "Count only the subsidy the scheme gives, and fund the "
                            "difference from the promoter or the loan.",
                            [best_rule],
                        )
                    )
                    gap(
                        best_rule,
                        "Capital subsidy",
                        f"At most {best:g} lakh ({how})",
                        f"{subsidy:g} lakh",
                        "Count only the admissible subsidy.",
                    )

    sector = project_sector(fin)
    for rule in rules:
        if covers(rule, sector) is False:
            what = SECTOR_LABEL.get(sector, sector)
            findings.append(
                _finding(
                    f"SCHEME_ACTIVITY_{rule.key.upper()}",
                    "policy",
                    f"{rule.name} does not cover this kind of project",
                    f"This DPR is for {what}; {rule.name} covers "
                    + ", ".join(SECTOR_LABEL.get(x, x) for x in sorted(rule.sectors))
                    + ".",
                    "Confirm the scheme with your bank, or apply under one that "
                    "covers the project.",
                    [rule],
                )
            )
            gap(
                rule,
                "Eligible activity",
                ", ".join(SECTOR_LABEL.get(x, x) for x in sorted(rule.sectors)),
                what,
                "Apply under a scheme that covers this project.",
            )
        if rule.project_cost_cap_lakh and cost and cost > rule.project_cost_cap_lakh:
            findings.append(
                _finding(
                    f"SCHEME_COST_CAP_{rule.key.upper()}",
                    "funding",
                    f"Project cost above the {rule.name} limit",
                    f"The project costs {cost:g} lakh; {rule.name} covers projects "
                    f"up to {rule.project_cost_cap_lakh:g} lakh, so the scheme "
                    "benefit applies to that amount at most.",
                    "Confirm with the bank how the cost above the limit is "
                    "financed, and compute the subsidy on the capped cost.",
                    [rule],
                )
            )
            gap(
                rule,
                "Project cost ceiling",
                f"Up to {rule.project_cost_cap_lakh:g} lakh",
                f"{cost:g} lakh",
                "Compute the benefit on the capped cost.",
            )
        if rule.own_contribution_min_pct and cost and own is not None:
            share = own / cost * 100
            if share < rule.own_contribution_min_pct * (1 - TOLERANCE):
                findings.append(
                    _finding(
                        f"SCHEME_OWN_SHARE_{rule.key.upper()}",
                        "funding",
                        f"Own contribution below the {rule.name} minimum",
                        f"The promoter contributes {own:g} lakh, {share:.1f}% of the "
                        f"project cost; {rule.name} requires at least "
                        f"{rule.own_contribution_min_pct:g}%.",
                        "Raise the promoter's contribution to the scheme minimum.",
                        [rule],
                    )
                )
                gap(
                    rule,
                    "Promoter contribution",
                    f"At least {rule.own_contribution_min_pct:g}%",
                    f"{share:.1f}%",
                    "Raise the contribution to the minimum.",
                )

    aif = next((r for r in rules if r.key == "aif"), None)
    if aif is not None:
        rate = fin.subvention_rate_pct
        if rate is not None and rate > aif.subvention_pct + 1e-9:
            findings.append(
                _finding(
                    "SCHEME_SUBVENTION_RATE",
                    "interest_subvention",
                    "Interest subvention above the AIF rate",
                    f"The DPR assumes {rate:g}% interest subvention; AIF gives "
                    f"{aif.subvention_pct:g}% a year.",
                    "Use the 3% AIF subvention in the interest and DSCR computations.",
                    [aif],
                )
            )
            gap(
                aif,
                "Interest subvention",
                f"{aif.subvention_pct:g}% a year",
                f"{rate:g}%",
                "Use 3% in the interest and DSCR computations.",
            )
        loan = fin.term_loan_amount
        if (
            loan.status == "extracted"
            and loan.value
            and loan.value > aif.subvention_loan_cap_lakh
        ):
            findings.append(
                _finding(
                    "SCHEME_SUBVENTION_LOAN_CAP",
                    "interest_subvention",
                    "AIF subvention applies only to the first Rs 2 crore",
                    f"The term loan is {loan.value:g} lakh; AIF's subvention covers "
                    f"loans up to {aif.subvention_loan_cap_lakh:g} lakh.",
                    "Compute interest at the full rate on the part of the loan "
                    "above Rs 2 crore.",
                    [aif],
                )
            )
            gap(
                aif,
                "Loan covered by subvention",
                f"Up to {aif.subvention_loan_cap_lakh:g} lakh",
                f"{loan.value:g} lakh",
                "Charge the full rate on the excess.",
            )
    return findings, records
