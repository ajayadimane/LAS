"""Sector benchmarks from a fixed, credit-reviewed table, checked in code.

Why a table and not web search: the same 1200-acre plan for the same
14-ft self-propelled harvester was flagged on two DPRs and not on two
others, because each DPR's cached search returned a different machine class
(15-25 acres/day for large self-propelled machines, 8-14 for tractor-
mounted ones) and different season lengths (40 to 300 operating days a
year). A search cannot know which machine the DPR buys; the DPR says so,
and the benchmark for that machine has to be the same every time.

So:
1. detect_harvester_machine reads the machine class from the DPR's own
   text (self-propelled or tractor-mounted, cutter-bar width, engine HP) --
   regex, no LLM, the same answer every run.
2. HARVESTER_BENCHMARKS gives one entry per machine class. Each entry says
   whether it is a draft or approved by credit, on what basis, and by whom.
3. run_benchmark_checks compares the DPR's planned annual acreage, its
   stated acres per day and its rate per acre with that entry. Findings are
   should-fix (an external figure is evidence for the reviewer, not
   arithmetic on the DPR's own figures) and carry a check_id, so the
   verifier can neither drop nor downgrade them.

A machine whose class cannot be read is compared with the largest class
(the most lenient figure), so a flag always means "beyond even the largest
machine". Web-search benchmarks (grounding.py) remain market evidence in
Annexure D, and are used for findings only in sectors this table does not
cover.

Polyhouse, dairy and cold-storage DPRs are checked the same way (sections
at the end): the structure type picks a cost norm per m2 and the crop a
yield and price; the breed picks a milk yield and price; the storage type
picks a cost norm per tonne, with the 3.4 m3-per-tonne capacity norm and the
potato rent range.

TO UPDATE A FIGURE: change it below, set status "approved", reviewed_by and
reviewed_on, and say in `basis` where it came from. Pure functions; no LLM,
no I/O.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .financial_calc import CalculationResult
from .financial_schema import ExtractedValue, ProjectFinancials
from .finding_rules import compute_dedup_key
from .schemas import Finding

# A DPR figure this far beyond the benchmark is flagged.
BENCHMARK_TOLERANCE = 0.10


@dataclass(frozen=True)
class Range:
    low: float
    high: float

    @property
    def typical(self) -> float:
        return (self.low + self.high) / 2


@dataclass(frozen=True)
class Benchmark:
    key: str
    label: str
    acres_per_day: Range
    operating_days_per_year: Range
    rate_per_acre_rupees: Range | None
    status: str  # "draft" (pending credit review) or "approved"
    basis: str
    reviewed_by: str | None = None
    reviewed_on: str | None = None

    @property
    def typical_acres_per_year(self) -> float:
        return round(self.acres_per_day.typical * self.operating_days_per_year.typical)

    @property
    def max_acres_per_year(self) -> float:
        return round(self.acres_per_day.high * self.operating_days_per_year.high)

    def cite(self) -> str:
        return f"LAS benchmark table ({self.label}; {_status_text(self)})"


def _status_text(entry) -> str:
    if entry.status == "approved":
        return f"approved {entry.reviewed_on or ''}".strip()
    return "draft, pending credit review"


def _references(entry, **items) -> dict:
    """{"price": {...}, "yield": {...}} for the page-3 assumptions table:
    the DPR's figure next to the table's range, for each item given as
    (dpr value or None, Range or None, unit). Items without both are left
    out."""
    out = {}
    for name, (dpr, band, unit) in items.items():
        if dpr is None or band is None:
            continue
        out[name.rstrip("_")] = {
            "dpr": dpr,
            "low": band.low,
            "high": band.high,
            "unit": unit,
            "source": "LAS benchmark, "
            + entry.label.split(" (")[0].split(", ")[0]
            + ("" if entry.status == "approved" else ", draft"),
        }
    return out


# Approved for use 2026-11-13 by the LAS project team, as provisional
# figures until credit reviews them (values may change then; see
# _REVIEWED_BY). Set 2026-11-13. NOT from any credit, bank or official
# dataset. Two inputs, and judgement where they disagree:
#   (a) what the evaluation DPRs claim about their own machines -- the
#       borrower's side, so weak evidence;
#   (b) web-search results cached during the evaluation runs
#       (eval/grounding_cache/), mostly secondary sites, a few university
#       and government pages.
#
# self_propelled_large
#   acres/day 12-20. DPRs: 91364956 "KARTAR 4000 SV can harvest 15 acres
#     per day" (plans 10-12); c0aa 1.5-2.0 acres/hour; 3e1c, e778 2.5
#     acres/hour x 8 h = 20. Search: 12-25 (combine-harvesters.com), 15-25
#     (kartarcombine.com, khetisetu.com), 8-15 (tnau.ac.in and others).
#     Judgement: 12 = low end of search; 20 = highest DPR claim; 25 left out.
#   days/year 60-90. Search: 60-90 (groundreport.in), 55-85 and 40-70
#     (autopunditz.com), 70-100 (tnau.ac.in, agriculture.institute), 60-120
#     (scribd.com). Judgement: where most results fall; 120-300
#     (agridots.com) discarded as counting all hiring work, not harvest days.
#   rate 1400-2200 rupees/acre. Search: 1400-2200 (khetisetu.com,
#     accio.com), 1600-2400 (thehindu.com, tnau.ac.in), 1800-2800; one
#     outlier 700-1200. DPRs charge 1800-2200. Judgement: the commonest range.
# self_propelled_small -- NO SOURCE. Scaled from the large class by cutter
#   width (7 ft = half of 14 ft). Contradicted by the one 7-ft DPR (4e9c),
#   which claims 12-15 acres/day. Weakest entry.
# tractor_mounted
#   acres/day 5-8. Search: 6 (maharashtra.gov.in), 8 (tnau.ac.in,
#     tractorkarvan.com). Rate 1000-1800: judgement, no source.
# days/year, all classes: the same central-India search figure; no
#   per-state figures.
#
# Better sources for credit review: harvester test reports from the
# government farm-machinery testing institute at Budni (field capacity);
# state agriculture / agricultural-engineering departments' custom-hiring
# rates; NABARD model schemes and unit costs; the bank's appraisal norms.
_SEASON_DAYS = Range(60, 90)
_REVIEWED_BY = "LAS project team (provisional, pending credit review)"
_REVIEWED_ON = "2026-11-13"
_DAYS_BASIS = (
    "Operating days: the most common range in public web sources (mainly "
    "secondary sites, some university pages), central India, no state "
    "breakdown."
)
HARVESTER_BENCHMARKS: dict[str, Benchmark] = {
    "self_propelled_large": Benchmark(
        key="self_propelled_large",
        label="self-propelled combine, 12-14 ft cutter bar, about 100-135 HP",
        acres_per_day=Range(12, 20),
        operating_days_per_year=_SEASON_DAYS,
        rate_per_acre_rupees=Range(1400, 2200),
        status="approved",
        reviewed_by=_REVIEWED_BY,
        reviewed_on=_REVIEWED_ON,
        basis=(
            "Not from an official or bank dataset. Acres/day: low end of "
            "public web sources (12) to the highest capacity claimed in "
            "DPRs reviewed (2.5 acres/hour x 8 hours = 20). Rate: the most "
            "common range in public web sources. " + _DAYS_BASIS
        ),
    ),
    "self_propelled_small": Benchmark(
        key="self_propelled_small",
        label="self-propelled combine, cutter bar under 12 ft",
        acres_per_day=Range(6, 10),
        operating_days_per_year=_SEASON_DAYS,
        rate_per_acre_rupees=Range(1200, 2000),
        status="approved",
        reviewed_by=_REVIEWED_BY,
        reviewed_on=_REVIEWED_ON,
        basis=(
            "No source: acres/day and rate scaled from the 12-14 ft class by "
            "cutter-bar width (an estimate, not a measurement). " + _DAYS_BASIS
        ),
    ),
    "tractor_mounted": Benchmark(
        key="tractor_mounted",
        label="tractor-mounted combine",
        acres_per_day=Range(5, 8),
        operating_days_per_year=_SEASON_DAYS,
        rate_per_acre_rupees=Range(1000, 1800),
        status="approved",
        reviewed_by=_REVIEWED_BY,
        reviewed_on=_REVIEWED_ON,
        basis=(
            "Acres/day: two public web sources (6 and 8 acres/day). Rate: an "
            "estimate with no source. " + _DAYS_BASIS
        ),
    ),
}
# The class used when the DPR does not say which machine it buys: the
# largest, so only plans beyond any machine are flagged.
MOST_LENIENT = "self_propelled_large"
LARGE_CUTTER_BAR_FT = 12
LARGE_ENGINE_HP = 100

_SELF_PROPELLED = re.compile(r"self[\s\-]*prop\w*", re.I)  # also "PROPELIED"
_OWN_ENGINE = re.compile(r"ashok\s*ley\s*l?and|cummins|kirloskar\s+engine", re.I)
_TRACTOR_MOUNTED = re.compile(r"tractor[\s\-]*(?:mounted|driven|operated|drawn)", re.I)
_CUTTER_BAR = (
    # "Cutter Bar : 14. FT", "CUTTER BAR SWATH 4267 MM (14 FEET", "Cutter
    # Size : 14 FT", "Cutter Swath:** 14 FEET"
    re.compile(
        r"cutter\s*(?:bar|size|swath|width)(?:\s*(?:swath|width|size))?"
        r"[\s:*\-]*(?:\d{3,4}\s*mm\s*\(\s*)?(\d{1,2}(?:\.\d)?)\s*\.?\s*(?:ft|feet)\b",
        re.I,
    ),
    # "14FT cutter bar", "7 FT CUTTER BAR", "14 Ft. cutter bar"
    re.compile(r"(\d{1,2}(?:\.\d)?)\s*(?:ft|feet)\.?\s*cutter", re.I),
)
_HP = re.compile(r"(?<![\d.])(\d{2,3})\s*h\.?\s?p\b", re.I)


@dataclass(frozen=True)
class HarvesterMachine:
    machine_class: str | None  # a HARVESTER_BENCHMARKS key, or None
    self_propelled: bool
    tractor_mounted: bool
    cutter_bar_ft: float | None
    engine_hp: float | None
    pages: tuple[int, ...] = field(default=())

    def describe(self) -> str:
        parts = []
        if self.self_propelled:
            parts.append("self-propelled")
        elif self.tractor_mounted:
            parts.append("tractor-mounted")
        if self.cutter_bar_ft:
            parts.append(f"{self.cutter_bar_ft:g} ft cutter bar")
        if self.engine_hp:
            parts.append(f"{self.engine_hp:g} HP")
        return ", ".join(parts) or "machine type not stated"


def _most_common(values: list[float]) -> float | None:
    return Counter(values).most_common(1)[0][0] if values else None


def detect_harvester_machine(pages: list) -> HarvesterMachine:
    """The harvester the DPR buys, as printed. An own engine (Ashok
    Leyland, or 90 HP and above) means self-propelled even where the words
    are missing; "tractor-mounted" counts only when nothing says
    self-propelled (seen live: a self-propelled DPR describing competitors
    as "tractor-mounted units")."""
    self_propelled = tractor = False
    cutter: list[float] = []
    hp: list[float] = []
    cited: set[int] = set()
    for page in pages:
        text = page.text or ""
        hit = False
        if _SELF_PROPELLED.search(text) or _OWN_ENGINE.search(text):
            self_propelled = hit = True
        if _TRACTOR_MOUNTED.search(text):
            tractor = True
        for pattern in _CUTTER_BAR:
            for m in pattern.finditer(text):
                value = float(m.group(1))
                if 4 <= value <= 20:
                    cutter.append(value)
                    hit = True
        for m in _HP.finditer(text):
            value = float(m.group(1))
            if 25 <= value <= 300:
                hp.append(value)
                hit = True
        if hit:
            cited.add(page.page)
    cutter_ft, engine_hp = _most_common(cutter), _most_common(hp)
    if engine_hp is not None and engine_hp >= 90:
        self_propelled = True
    if self_propelled:
        if cutter_ft is not None:
            machine_class = (
                "self_propelled_large"
                if cutter_ft >= LARGE_CUTTER_BAR_FT
                else "self_propelled_small"
            )
        elif engine_hp is not None and engine_hp < LARGE_ENGINE_HP:
            machine_class = "self_propelled_small"
        else:
            machine_class = "self_propelled_large"
    elif tractor:
        machine_class = "tractor_mounted"
    else:
        machine_class = None
    return HarvesterMachine(
        machine_class=machine_class,
        self_propelled=self_propelled,
        tractor_mounted=tractor and not self_propelled,
        cutter_bar_ft=cutter_ft,
        engine_hp=engine_hp,
        pages=tuple(sorted(cited))[:5],
    )


def benchmark_for(machine: HarvesterMachine) -> Benchmark:
    return HARVESTER_BENCHMARKS[machine.machine_class or MOST_LENIENT]


def _ev(value: ExtractedValue | None) -> tuple[float, int | None] | None:
    if value is None or value.status != "extracted" or not value.value:
        return None
    return value.value, value.page


def planned_acres_per_year(fin: ProjectFinancials) -> tuple[float, int | None] | None:
    """The DPR's annual acreage: the harvester group's acres per year, else
    the largest yearly volume, else the stated annual capacity (the model
    files the same printed target under different fields run to run)."""
    h = fin.harvester_assumptions
    if h is not None and (acres := _ev(h.acres_per_year)):
        return acres
    ue = fin.unit_economics
    if ue is None or (ue.unit_name or "acre").casefold().rstrip("s") != "acre":
        return None
    sold = [(y.units_sold, y.page) for y in ue.years if y.units_sold]
    if sold:
        return max(sold, key=lambda s: s[0])
    return _ev(ue.capacity_per_year)


def _rate_per_acre(fin: ProjectFinancials) -> tuple[float, int | None] | None:
    h = fin.harvester_assumptions
    if h is not None and (rate := _ev(h.price_per_acre)):
        return rate
    ue = fin.unit_economics
    if ue is not None and (ue.unit_name or "").casefold().rstrip("s") == "acre":
        return _ev(ue.price_per_unit_rupees)
    return None


def is_harvester(fin: ProjectFinancials | None) -> bool:
    return fin is not None and fin.harvester_assumptions is not None


def _finding(
    *,
    check_id: str,
    category: str,
    title: str,
    explanation: str,
    action: str,
    page: int | None,
    benchmark,
) -> Finding:
    f = Finding(
        category=category,
        severity="medium",
        title=title,
        explanation=(
            f"{explanation} Benchmark: LAS benchmark table ({benchmark.label}; "
            f"{_status_text(benchmark)})."
        ),
        evidence_page=page,
        suggested_action=action,
        provenance="calculated",
        verification="source_verified",
        check_id=check_id,
    )
    f.priority = "should_fix"
    f.priority_rule = (
        "SHOULD_FIX: DPR figure beyond the benchmark table for its machine "
        "class (benchmarks.py); external benchmarks are capped at should-fix."
    )
    f.finding_class = "business_risk"
    f.dedup_key = compute_dedup_key(f)
    return f


def _harvester_checks(
    fin: ProjectFinancials, pages: list, *, acreage_flagged: bool = False
) -> tuple[list[CalculationResult], list[Finding], dict | None]:
    """(calculations, findings, record) for a harvester DPR; nothing for any
    other sector. `acreage_flagged`: the harvester engine already found the
    acreage beyond the DPR's own per-day x days figures -- the stronger
    evidence -- so a benchmark acreage finding would repeat it. When the
    DPR's own figures agree with each other (seen live: 20 acres/day x 80
    days = 1600), only the benchmark can question them, so it still runs."""
    machine = detect_harvester_machine(pages)
    bench = benchmark_for(machine)
    machine_text = machine.describe()
    results: list[CalculationResult] = []
    findings: list[Finding] = []

    planned = planned_acres_per_year(fin)
    if planned is not None:
        acres, page = planned
        typical = bench.typical_acres_per_year
        over = acres > typical * (1 + BENCHMARK_TOLERANCE)
        results.append(
            CalculationResult(
                "benchmark_acres_per_year",
                computed_value=typical,
                stated_value=acres,
                match=not over,
                discrepancy=round(acres - typical, 2),
                note=bench.key,
            )
        )
        if over and not acreage_flagged:
            beyond = acres > bench.max_acres_per_year
            findings.append(
                _finding(
                    check_id="BENCHMARK_ACREAGE",
                    category="assumption",
                    title="Planned annual acreage above the benchmark for this machine",
                    explanation=(
                        f"The DPR plans {acres:g} acres a year with a {machine_text} "
                        f"harvester. A machine of this class typically covers "
                        f"{bench.acres_per_day.typical:g} acres a day over about "
                        f"{bench.operating_days_per_year.typical:g} operating days, "
                        f"i.e. about {typical:g} acres a year "
                        f"({bench.max_acres_per_year:g} at the top of the ranges"
                        + (", which the plan exceeds too" if beyond else "")
                        + ")."
                    ),
                    action=(
                        "Base the annual acreage on the machine's rated capacity "
                        "and the local harvest windows, or show the booked demand "
                        "behind it."
                    ),
                    page=page,
                    benchmark=bench,
                )
            )

    h = fin.harvester_assumptions
    per_day = _ev(h.acres_per_day) if h is not None else None
    if per_day is not None:
        value, page = per_day
        over = value > bench.acres_per_day.high * (1 + BENCHMARK_TOLERANCE)
        results.append(
            CalculationResult(
                "benchmark_acres_per_day",
                computed_value=bench.acres_per_day.high,
                stated_value=value,
                match=not over,
                discrepancy=round(value - bench.acres_per_day.high, 2),
                note=bench.key,
            )
        )
        if over:
            findings.append(
                _finding(
                    check_id="BENCHMARK_ACRES_PER_DAY",
                    category="assumption",
                    title="Acres per day above the benchmark for this machine",
                    explanation=(
                        f"The DPR assumes {value:g} acres a day for a {machine_text} "
                        f"harvester; machines of this class cover "
                        f"{bench.acres_per_day.low:g}-{bench.acres_per_day.high:g} "
                        "acres a day."
                    ),
                    action=(
                        "Use the manufacturer's rated field capacity, net of "
                        "travel between fields."
                    ),
                    page=page,
                    benchmark=bench,
                )
            )

    rate = _rate_per_acre(fin)
    if rate is not None and bench.rate_per_acre_rupees is not None:
        value, page = rate
        band = bench.rate_per_acre_rupees
        over = value > band.high * (1 + BENCHMARK_TOLERANCE)
        results.append(
            CalculationResult(
                "benchmark_rate_per_acre",
                computed_value=band.high,
                stated_value=value,
                match=not over,
                discrepancy=round(value - band.high, 2),
                note=bench.key,
            )
        )
        if over:
            findings.append(
                _finding(
                    check_id="BENCHMARK_RATE",
                    category="market",
                    title="Rate per acre above the benchmark range",
                    explanation=(
                        f"The DPR charges {value:g} rupees an acre; custom-hiring "
                        f"rates for this machine class are {band.low:g}-"
                        f"{band.high:g} rupees an acre."
                    ),
                    action=(
                        "Support the rate with local quotations or lower it to "
                        "the market range."
                    ),
                    page=page,
                    benchmark=bench,
                )
            )

    record = {
        "sector": "harvester",
        "machine": machine_text,
        "machine_class": machine.machine_class,
        "machine_pages": list(machine.pages),
        "benchmark": bench.key,
        "benchmark_label": bench.label,
        "benchmark_status": bench.status,
        "benchmark_basis": bench.basis,
        "benchmark_reviewed_by": bench.reviewed_by,
        "benchmark_reviewed_on": bench.reviewed_on,
        "acres_per_day": [bench.acres_per_day.low, bench.acres_per_day.high],
        "operating_days_per_year": [
            bench.operating_days_per_year.low,
            bench.operating_days_per_year.high,
        ],
        "typical_acres_per_year": bench.typical_acres_per_year,
        "rate_per_acre_rupees": [band.low, band.high]
        if (band := bench.rate_per_acre_rupees)
        else None,
    }
    annexure = {
        "Machine (read from the DPR)": machine_text,
        "Benchmark used": (
            f"{bench.label}: {bench.acres_per_day.low:g}-"
            f"{bench.acres_per_day.high:g} acres/day, "
            f"{bench.operating_days_per_year.low:g}-"
            f"{bench.operating_days_per_year.high:g} operating days/year, "
            f"typically {bench.typical_acres_per_year:g} acres/year "
            f"({_status_text(bench)})"
        ),
    }
    if band := bench.rate_per_acre_rupees:
        annexure["Benchmark rate per acre"] = f"{band.low:g}-{band.high:g} rupees"
    annexure["Benchmark basis"] = bench.basis
    record["annexure"] = annexure
    record["reference"] = _references(
        bench,
        price=(rate[0] if rate else None, bench.rate_per_acre_rupees, "acre"),
        yield_=(per_day[0] if per_day else None, bench.acres_per_day, "acres a day"),
    )
    return results, findings, record


# --- Polyhouse (protected cultivation) --------------------------------------
#
# Approved for use 2026-11-13 by the LAS project team, provisional until
# credit reviews them -- like the harvester figures above. Sources (searched
# 2026-11-13):
#   structure cost, naturally ventilated (NVPH): NHB cost norm Rs 844/m2
#     (plains; nhb.gov.in "Cost Norms"); state schedules Rs 935/m2 (Assam
#     HMNEH 2018-19, above 500 up to 1008 m2; SLBC Madhya Pradesh unit cost
#     2019-20, Rs 9.35 lakh per 1000 m2) and Rs 950/m2 (West Bengal, 2080-
#     4000 m2 band). A parliamentary answer (eparlib.nic.in) gives Rs 844-
#     1650/m2 across all sizes and types.
#   other structure types (fan-and-pad, shade net): no figure found; the
#     unknown entry uses the top of the parliamentary range (lenient).
#   Dutch rose yield, stems per m2 per year: 100-200 (secondary guides:
#     agrifarming.in, a Slideshare lecture); 142-145 (IIHR Arka Swadesh
#     variety page, iihr.res.in; a 2023 NVPH variety trial, zenodo.org).
#     No NHB model-project figure found -- the source to ask credit for.
#   Dutch rose price per stem: consumer price about Rs 7.64 with a 72%
#     producer share, i.e. about Rs 5.5 at the farm (Pune-area thesis,
#     krishikosh.egranth.ac.in); Rs 3 (tractorjunction.com) to Rs 3-15 by
#     season and quality (organicmandya.com, unsourced). Judgement: 3-8.


@dataclass(frozen=True)
class StructureNorm:
    key: str
    label: str
    cost_per_sq_m_rupees: Range
    status: str
    basis: str
    reviewed_by: str | None = None
    reviewed_on: str | None = None


@dataclass(frozen=True)
class CropNorm:
    key: str
    label: str
    yield_per_sq_m_per_year: Range  # stems (or the crop's unit) per m2 a year
    price_per_unit_rupees: Range
    unit: str
    status: str
    basis: str
    reviewed_by: str | None = None
    reviewed_on: str | None = None


POLYHOUSE_STRUCTURES: dict[str, StructureNorm] = {
    "nvph": StructureNorm(
        key="nvph",
        label="naturally ventilated polyhouse (NVPH)",
        cost_per_sq_m_rupees=Range(844, 950),
        status="approved",
        reviewed_by=_REVIEWED_BY,
        reviewed_on=_REVIEWED_ON,
        basis=(
            "Official cost norms: NHB Rs 844/m2 (plains); state horticulture "
            "schedules Rs 935-950/m2 (Assam, Madhya Pradesh SLBC, West Bengal)."
        ),
    ),
    "unknown": StructureNorm(
        key="unknown",
        label="polyhouse of unstated type",
        cost_per_sq_m_rupees=Range(844, 1650),
        status="approved",
        reviewed_by=_REVIEWED_BY,
        reviewed_on=_REVIEWED_ON,
        basis=(
            "Official range across sizes and types, Rs 844-1650/m2 "
            "(parliamentary answer on MIDH/NHB norms); used when the DPR does "
            "not state a structure type that has its own norm."
        ),
    ),
}
POLYHOUSE_CROPS: dict[str, CropNorm] = {
    "dutch_rose": CropNorm(
        key="dutch_rose",
        label="Dutch rose (cut flower)",
        yield_per_sq_m_per_year=Range(100, 200),
        price_per_unit_rupees=Range(3, 8),
        unit="stem",
        status="approved",
        reviewed_by=_REVIEWED_BY,
        reviewed_on=_REVIEWED_ON,
        basis=(
            "Not from an official dataset. Yield: 100-200 stems/m2/year in "
            "public growing guides; 142-145 in an IIHR variety release and a "
            "university trial. Price: about Rs 5.5/stem at the farm in one "
            "market study; Rs 3-15 in growing guides. No NHB model-project "
            "figure was found."
        ),
    ),
}
_NVPH = re.compile(r"\bnvph\b|naturally\s+ventilated", re.I)
_CROPS = (("dutch_rose", re.compile(r"\brose", re.I)),)
# Price per unit above this is not per stem/piece (e.g. per kg or per box):
# the yield implied from revenue / price would be meaningless.
_MAX_PIECE_PRICE = 50


def is_polyhouse(fin: ProjectFinancials | None) -> bool:
    return fin is not None and fin.polyhouse_assumptions is not None


def _structure_of(fin: ProjectFinancials, pages: list) -> StructureNorm:
    printed = fin.polyhouse_assumptions.structure_type or ""
    if _NVPH.search(printed) or (
        not printed and any(_NVPH.search(p.text or "") for p in pages)
    ):
        return POLYHOUSE_STRUCTURES["nvph"]
    return POLYHOUSE_STRUCTURES["unknown"]


def _crop_of(fin: ProjectFinancials) -> CropNorm | None:
    crop = fin.polyhouse_assumptions.crop or ""
    return next(
        (POLYHOUSE_CROPS[key] for key, pattern in _CROPS if pattern.search(crop)),
        None,
    )


def _yield_per_sq_m(
    fin: ProjectFinancials, area: float
) -> tuple[float, int | None, str] | None:
    """(yield per m2 a year, page, how it was obtained): as printed, else
    per plant x plants, else annual units / area, else implied from year-1
    revenue / price (when the price is clearly per piece)."""
    ph = fin.polyhouse_assumptions
    if stated := _ev(ph.yield_per_sq_m_per_year):
        return stated[0], stated[1], "as printed"
    per_plant, plants = _ev(ph.yield_per_plant_per_year), _ev(ph.plants)
    if per_plant and plants:
        return (
            round(per_plant[0] * plants[0] / area, 1),
            per_plant[1],
            f"{per_plant[0]:g} per plant x {plants[0]:g} plants / {area:g} m2",
        )
    if units := _ev(ph.units_per_year):
        return (
            round(units[0] / area, 1),
            units[1],
            f"{units[0]:g} a year / {area:g} m2",
        )
    price = _ev(ph.price_per_unit_rupees)
    revenue_1 = next((y.revenue for y in fin.years if y.year == 1), None)
    if price and revenue_1 and price[0] <= _MAX_PIECE_PRICE:
        units_1 = revenue_1 * 100_000 / price[0]
        return (
            round(units_1 / area, 1),
            price[1],
            f"implied: year-1 revenue {revenue_1:g} lakh / {price[0]:g} rupees "
            f"= {units_1:,.0f} a year / {area:g} m2",
        )
    return None


def _polyhouse_checks(
    fin: ProjectFinancials, pages: list
) -> tuple[list[CalculationResult], list[Finding], dict | None]:
    ph = fin.polyhouse_assumptions
    structure = _structure_of(fin, pages)
    crop = _crop_of(fin)
    results: list[CalculationResult] = []
    findings: list[Finding] = []
    area_ev = _ev(ph.area_sq_m)
    area = area_ev[0] if area_ev else None

    cost = _ev(ph.structure_cost_lakh)
    if cost and area:
        per_sq_m = round(cost[0] * 100_000 / area)
        band = structure.cost_per_sq_m_rupees
        over = per_sq_m > band.high * (1 + BENCHMARK_TOLERANCE)
        results.append(
            CalculationResult(
                "benchmark_structure_cost_per_sq_m",
                computed_value=band.high,
                stated_value=per_sq_m,
                match=not over,
                discrepancy=round(per_sq_m - band.high, 2),
                note=structure.key,
            )
        )
        if over:
            findings.append(
                _finding(
                    check_id="BENCHMARK_STRUCTURE_COST",
                    category="project_cost",
                    title="Polyhouse structure cost above the subsidy cost norm",
                    explanation=(
                        f"The structure costs {cost[0]:g} lakh for {area:g} m2, "
                        f"i.e. {per_sq_m:,} rupees per m2. Subsidy for a "
                        f"{structure.label} is computed on at most "
                        f"{band.low:g}-{band.high:g} rupees per m2; the rest of "
                        "the cost earns no subsidy."
                    ),
                    action=(
                        "Count subsidy only on the norm cost in the means of "
                        "finance, fund the excess, and support the cost with a "
                        "quotation itemised to the norm's specification."
                    ),
                    page=cost[1],
                    benchmark=structure,
                )
            )

    yield_used = _yield_per_sq_m(fin, area) if area else None
    if crop and yield_used:
        value, page, how = yield_used
        band = crop.yield_per_sq_m_per_year
        over = value > band.high * (1 + BENCHMARK_TOLERANCE)
        results.append(
            CalculationResult(
                "benchmark_yield_per_sq_m",
                computed_value=band.high,
                stated_value=value,
                match=not over,
                discrepancy=round(value - band.high, 2),
                note=crop.key,
            )
        )
        if over:
            findings.append(
                _finding(
                    check_id="BENCHMARK_YIELD",
                    category="assumption",
                    title="Crop yield above the benchmark",
                    explanation=(
                        f"The DPR's {crop.label} yield is {value:g} {crop.unit}s "
                        f"per m2 a year ({how}); published yields are "
                        f"{band.low:g}-{band.high:g}."
                    ),
                    action=(
                        "Base the yield on the variety's published yield and "
                        "the planting density, with a source."
                    ),
                    page=page,
                    benchmark=crop,
                )
            )

    price = _ev(ph.price_per_unit_rupees)
    unit = (ph.produce_unit or "").casefold()
    per_piece = unit.rstrip("s") in ("stem", "bunch", "flower", "piece", "")
    if crop and price and per_piece and price[0] <= _MAX_PIECE_PRICE:
        band = crop.price_per_unit_rupees
        over = price[0] > band.high * (1 + BENCHMARK_TOLERANCE)
        results.append(
            CalculationResult(
                "benchmark_price_per_unit",
                computed_value=band.high,
                stated_value=price[0],
                match=not over,
                discrepancy=round(price[0] - band.high, 2),
                note=crop.key,
            )
        )
        if over:
            findings.append(
                _finding(
                    check_id="BENCHMARK_RATE",
                    category="market",
                    title="Sale price above the benchmark range",
                    explanation=(
                        f"The DPR sells at {price[0]:g} rupees per "
                        f"{ph.produce_unit or crop.unit}; {crop.label} sells at "
                        f"{band.low:g}-{band.high:g} rupees per {crop.unit} at "
                        "the farm."
                    ),
                    action=(
                        "Support the price with buyer quotations or mandi "
                        "records, or lower it to the market range."
                    ),
                    page=price[1],
                    benchmark=crop,
                )
            )

    annexure = {
        "Structure (read from the DPR)": " ".join(
            x
            for x in (
                ph.structure_type,
                f"{area:g} m2" if area else None,
                f"growing {ph.crop}" if ph.crop else None,
            )
            if x
        )
        or "not stated",
        "Benchmark used (structure cost)": (
            f"{structure.label}: {structure.cost_per_sq_m_rupees.low:g}-"
            f"{structure.cost_per_sq_m_rupees.high:g} rupees/m2 "
            f"({_status_text(structure)})"
        ),
        "Benchmark basis (structure cost)": structure.basis,
    }
    if crop:
        annexure["Benchmark used (crop)"] = (
            f"{crop.label}: {crop.yield_per_sq_m_per_year.low:g}-"
            f"{crop.yield_per_sq_m_per_year.high:g} {crop.unit}s/m2/year, "
            f"{crop.price_per_unit_rupees.low:g}-"
            f"{crop.price_per_unit_rupees.high:g} rupees/{crop.unit} "
            f"({_status_text(crop)})"
        )
        annexure["Benchmark basis (crop)"] = crop.basis
    if yield_used:
        annexure["Yield per m2 (computed)"] = f"{yield_used[0]:g} ({yield_used[2]})"
    record = {
        "sector": "polyhouse",
        "benchmark": structure.key + (f"+{crop.key}" if crop else ""),
        "annexure": annexure,
    }
    if crop:
        record["reference"] = _references(
            crop,
            price=(
                price[0] if price and per_piece else None,
                crop.price_per_unit_rupees,
                crop.unit,
            ),
            yield_=(
                yield_used[0] if yield_used else None,
                crop.yield_per_sq_m_per_year,
                f"{crop.unit}s a m2 a year",
            ),
        )
    return results, findings, record


# --- Dairy -------------------------------------------------------------------
#
# Approved for use 2026-11-13 by the LAS project team, provisional until
# credit reviews them. Sources (searched 2026-11-13):
#   milk yield, litres per animal per day in milk:
#     crossbred cow: national average 7.61 kg/day (BAHS 2017-18, cited by
#       a NABARD-model summary on scribd.com); NABARD-style appraisal guide
#       10 litres/day (2007, scribd.com). Blogs claiming 15-25 excluded.
#       Judgement: 7-12.
#     buffalo (Murrah): pooled 6.34 kg/day across studies, 6.85 (Buffalo
#       Bulletin review, kuojs.lib.ku.ac.th); Punjab 8.44, national 5.62
#       (BAHS 2019, same review); NABARD's 10-buffalo model project assumes
#       10 litres/day for graded Murrah (agritech.tnau.ac.in). Range 6-10:
#       first set at 6-9, which flagged NABARD's own model (seen live).
#     indigenous cow: national 3.73 kg/day (BAHS 2017-18); NABARD model
#       bankable scheme on indigenous cow 4 litres/day (vikaspedia.in).
#       Judgement: 3-5.
#   lactation: 270 days in milk and 120-150 dry days (NABARD model schemes,
#     vikaspedia.in, agritech.tnau.ac.in); standard lactation 305 days
#     (Murrah studies, arccjournals.com). Ceiling: 305 days.
#   milk price, rupees per litre at the farm: Indore milk union Rs 840 per
#     kg of fat (Sept 2025, dairynews7x7.com), i.e. about Rs 29-38 for cow
#     milk at 3.5-4.5% fat and Rs 50-59 for buffalo milk at 6-7% fat; Amul
#     Rs 40-43, Sanchi Rs 32-35 a litre (2023, 101reporters.com).
#     Judgement: cow 30-45, buffalo 45-65.


@dataclass(frozen=True)
class DairyNorm:
    key: str
    label: str
    yield_litres_per_day: Range
    price_per_litre_rupees: Range
    status: str
    basis: str
    reviewed_by: str | None = None
    reviewed_on: str | None = None


MAX_LACTATION_DAYS = 305
_DAIRY_BASIS = (
    "Not from an official dataset, except the national averages. Yield: "
    "national averages (Basic Animal Husbandry Statistics), NABARD model "
    "schemes and published breed studies. Lactation: at most 305 days in "
    "milk (standard lactation; NABARD models use 270). Price: milk-union "
    "procurement rates (fat-based) and press reports."
)
DAIRY_BREEDS: dict[str, DairyNorm] = {
    "crossbred_cow": DairyNorm(
        key="crossbred_cow",
        label="crossbred cow (HF / Jersey cross)",
        yield_litres_per_day=Range(7, 12),
        price_per_litre_rupees=Range(30, 45),
        status="approved",
        reviewed_by=_REVIEWED_BY,
        reviewed_on=_REVIEWED_ON,
        basis=_DAIRY_BASIS,
    ),
    "buffalo": DairyNorm(
        key="buffalo",
        label="buffalo (Murrah or graded)",
        yield_litres_per_day=Range(6, 10),
        price_per_litre_rupees=Range(45, 65),
        status="approved",
        reviewed_by=_REVIEWED_BY,
        reviewed_on=_REVIEWED_ON,
        basis=_DAIRY_BASIS,
    ),
    "indigenous_cow": DairyNorm(
        key="indigenous_cow",
        label="indigenous cow (Gir, Sahiwal...)",
        yield_litres_per_day=Range(3, 5),
        price_per_litre_rupees=Range(30, 45),
        status="approved",
        reviewed_by=_REVIEWED_BY,
        reviewed_on=_REVIEWED_ON,
        basis=_DAIRY_BASIS,
    ),
}
# Most specific first. An unstated breed gets the crossbred entry: the
# highest cow yield (lenient), and a cow price.
_BREEDS = (
    ("buffalo", re.compile(r"buffalo|murrah|mehsana|jaffarabadi|surti", re.I)),
    (
        "indigenous_cow",
        re.compile(
            r"\b(?:gir|sahiwal|tharparkar|red sindhi|rathi|"
            r"indigenous|desi)\b",
            re.I,
        ),
    ),
    (
        "crossbred_cow",
        re.compile(r"cross\s*-?\s*bred|\bhf\b|holstein|jersey|friesian", re.I),
    ),
)


def is_dairy(fin: ProjectFinancials | None) -> bool:
    return fin is not None and fin.dairy_assumptions is not None


def _breed_of(fin: ProjectFinancials, pages: list) -> tuple[DairyNorm, bool]:
    """(entry, read from the DPR?). The extracted animal type first, else
    the breed named most often in the pages."""
    printed = fin.dairy_assumptions.animal_type or ""
    for key, pattern in _BREEDS:
        if pattern.search(printed):
            return DAIRY_BREEDS[key], True
    counts = Counter(
        key
        for page in pages
        for key, pattern in _BREEDS
        for _ in pattern.finditer(page.text or "")
    )
    if counts:
        return DAIRY_BREEDS[counts.most_common(1)[0][0]], True
    return DAIRY_BREEDS["crossbred_cow"], False


def _dairy_checks(
    fin: ProjectFinancials, pages: list
) -> tuple[list[CalculationResult], list[Finding], dict]:
    d = fin.dairy_assumptions
    breed, known = _breed_of(fin, pages)
    results: list[CalculationResult] = []
    findings: list[Finding] = []

    def check(name, check_id, value, page, limit, finding_args):
        over = value > limit * (1 + BENCHMARK_TOLERANCE)
        results.append(
            CalculationResult(
                name,
                computed_value=limit,
                stated_value=value,
                match=not over,
                discrepancy=round(value - limit, 2),
                note=breed.key,
            )
        )
        if over:
            findings.append(
                _finding(check_id=check_id, page=page, benchmark=breed, **finding_args)
            )

    if milk := _ev(d.milk_yield_litres_per_day):
        band = breed.yield_litres_per_day
        check(
            "benchmark_milk_yield_per_day",
            "BENCHMARK_MILK_YIELD",
            milk[0],
            milk[1],
            band.high,
            dict(
                category="assumption",
                title="Milk yield above the benchmark for the breed",
                explanation=(
                    f"The DPR assumes {milk[0]:g} litres a day per animal for "
                    f"{breed.label}; the benchmark is {band.low:g}-{band.high:g}."
                ),
                action=(
                    "Base the yield on the animals' recorded yield (purchase "
                    "records or a veterinary certificate), net of the dry period."
                ),
            ),
        )
    if days := _ev(d.lactation_days):
        check(
            "benchmark_lactation_days",
            "BENCHMARK_LACTATION",
            days[0],
            days[1],
            MAX_LACTATION_DAYS,
            dict(
                category="assumption",
                title="Animals assumed in milk longer than a lactation",
                explanation=(
                    f"The DPR assumes {days[0]:g} days in milk; a lactation "
                    f"lasts at most about {MAX_LACTATION_DAYS} days, followed by "
                    "a dry period of 120-150 days."
                ),
                action=(
                    "Allow for the dry period: about 270 days in milk per "
                    "lactation, and calving intervals of 13-15 months."
                ),
            ),
        )
    animals, per_year = _ev(d.animals), _ev(d.milk_per_year_litres)
    if animals and per_year:
        per_animal = round(per_year[0] / animals[0])
        limit = breed.yield_litres_per_day.high * MAX_LACTATION_DAYS
        check(
            "benchmark_milk_per_animal_per_year",
            "BENCHMARK_MILK_PER_ANIMAL",
            per_animal,
            per_year[1],
            limit,
            dict(
                category="assumption",
                title="Annual milk per animal above what the breed gives",
                explanation=(
                    f"The DPR's {per_year[0]:g} litres a year from {animals[0]:g} "
                    f"animals is {per_animal:g} litres per animal; {breed.label} "
                    f"gives at most about {limit:g} "
                    f"({breed.yield_litres_per_day.high:g} litres a day for "
                    f"{MAX_LACTATION_DAYS} days)."
                ),
                action="Rebuild annual milk as animals x yield x days in milk.",
            ),
        )
    if price := _ev(d.milk_price_per_litre):
        band = breed.price_per_litre_rupees
        check(
            "benchmark_milk_price",
            "BENCHMARK_RATE",
            price[0],
            price[1],
            band.high,
            dict(
                category="market",
                title="Milk price above the benchmark range",
                explanation=(
                    f"The DPR sells milk at {price[0]:g} rupees a litre; "
                    f"{breed.label} milk fetches {band.low:g}-{band.high:g} "
                    "rupees a litre from milk unions and buyers."
                ),
                action=(
                    "Support the price with the buyer's rate card (fat/SNF "
                    "basis) or a supply agreement."
                ),
            ),
        )

    annexure = {
        "Herd (read from the DPR)": " ".join(
            x
            for x in (
                f"{animals[0]:g}" if animals else None,
                d.animal_type or None,
            )
            if x
        )
        or "not stated",
        "Benchmark used (dairy)": (
            f"{breed.label}"
            + ("" if known else " (breed not stated; the most lenient cow entry)")
            + f": {breed.yield_litres_per_day.low:g}-"
            f"{breed.yield_litres_per_day.high:g} litres/day in milk, at most "
            f"{MAX_LACTATION_DAYS} days in milk, "
            f"{breed.price_per_litre_rupees.low:g}-"
            f"{breed.price_per_litre_rupees.high:g} rupees/litre "
            f"({_status_text(breed)})"
        ),
        "Benchmark basis (dairy)": breed.basis,
    }
    return (
        results,
        findings,
        {
            "sector": "dairy",
            "benchmark": breed.key,
            "annexure": annexure,
            "reference": _references(
                breed,
                price=(
                    price[0] if price else None,
                    breed.price_per_litre_rupees,
                    "litre",
                ),
                yield_=(
                    milk[0] if milk else None,
                    breed.yield_litres_per_day,
                    "litres a day per animal",
                ),
            ),
        },
    )


# --- Cold storage ------------------------------------------------------------
#
# Approved for use 2026-11-13 by the LAS project team, provisional until
# credit reviews them. Sources (searched 2026-11-13):
#   capital cost per tonne of capacity: MIDH/NHB cost norms (nhb.gov.in
#     "Cost Norms"; Bihar horticulture model project, Type 2): Type 1 (single
#     temperature zone) Rs 8,000/MT, Type 2 (multiple temperature zones,
#     PEB) Rs 10,000/MT, up to 5,000 MT; lower per MT above 5,000 MT. A
#     secondary source (okcredit.in) cites the April 2025 MIDH guidelines
#     at Rs 9,600 (civil) and Rs 12,000 (PEB) -- unverified; used as the top
#     of each range so the newer norm is not flagged.
#   capacity: 3.4 m3 (120 cft) of chamber volume per MT (NHB norms). The
#     Bihar Type 2 model project's chambers match it (8 x 12x8x9 m = 6,912
#     m3 for 2,000 MT, 3.46 m3/MT), though its text also says 11 m3/MT.
#   The cost norm is what subsidy is computed on, not the market cost: the
#     same Bihar model costs Rs 23,400/MT (468 lakh for 2,000 MT, no land).
#     So the finding is about the subsidy cap, not an excessive cost.
#   potato rent, rupees per quintal per season: MP Rs 225 (2022, cold-chain
#     association, krishakjagat.org); UP Rs 230-260 fixed for 2023-24
#     (newsclick.in); Rs 260-280 in Mainpuri, 2026 (credenceresearch.com);
#     Rs 340-380 total charges, 2026 (freshplaza.com). Range 225-380.


@dataclass(frozen=True)
class ColdStorageNorm:
    key: str
    label: str
    cost_per_mt_rupees: Range
    status: str
    basis: str
    reviewed_by: str | None = None
    reviewed_on: str | None = None


CUBIC_M_PER_MT = 3.4
POTATO_RENT_PER_QUINTAL_SEASON = Range(225, 380)
_COLD_COST_BASIS = (
    "Official MIDH/NHB cost norms per tonne of capacity (Type 1 Rs 8,000, "
    "Type 2 Rs 10,000, up to 5,000 MT; a reported 2025 revision of Rs 9,600 "
    "and Rs 12,000 is used as the top of the range). Capacity: 3.4 m3 of "
    "chamber per tonne (NHB norm). Potato rent: Rs 225-380 a quintal a "
    "season (state-fixed rates and press reports, 2022-2026)."
)
COLD_STORAGE_TYPES: dict[str, ColdStorageNorm] = {
    "type_1": ColdStorageNorm(
        key="type_1",
        label="cold storage Type 1 (single temperature zone)",
        cost_per_mt_rupees=Range(8000, 9600),
        status="approved",
        reviewed_by=_REVIEWED_BY,
        reviewed_on=_REVIEWED_ON,
        basis=_COLD_COST_BASIS,
    ),
    "type_2": ColdStorageNorm(
        key="type_2",
        label="cold storage Type 2 (multiple temperature zones, PEB)",
        cost_per_mt_rupees=Range(10000, 12000),
        status="approved",
        reviewed_by=_REVIEWED_BY,
        reviewed_on=_REVIEWED_ON,
        basis=_COLD_COST_BASIS,
    ),
}
_TYPE_2 = re.compile(
    r"multi[\s\-]*(?:chamber|temperature|commodity)|\bpeb\b|pre[\s\-]*engineered|"
    r"type[\s\-]*(?:2|ii)\b|controlled\s+atmosphere",
    re.I,
)
_TYPE_1 = re.compile(r"single[\s\-]*(?:chamber|temperature)|type[\s\-]*(?:1|i)\b", re.I)


def is_cold_storage(fin: ProjectFinancials | None) -> bool:
    return fin is not None and fin.cold_storage_assumptions is not None


def _storage_type_of(
    fin: ProjectFinancials, pages: list
) -> tuple[ColdStorageNorm, bool]:
    """(entry, read from the DPR?). Type 2 when the DPR says multi-chamber,
    PEB or controlled atmosphere; Type 1 when it says single chamber; else
    Type 2 (the higher norm, lenient)."""
    printed = fin.cold_storage_assumptions.storage_type or ""
    texts = [printed] if printed else [p.text or "" for p in pages]
    if any(_TYPE_2.search(t) for t in texts):
        return COLD_STORAGE_TYPES["type_2"], True
    if any(_TYPE_1.search(t) for t in texts):
        return COLD_STORAGE_TYPES["type_1"], True
    return COLD_STORAGE_TYPES["type_2"], False


def _cold_storage_checks(
    fin: ProjectFinancials, pages: list
) -> tuple[list[CalculationResult], list[Finding], dict]:
    c = fin.cold_storage_assumptions
    norm, known = _storage_type_of(fin, pages)
    results: list[CalculationResult] = []
    findings: list[Finding] = []
    capacity, cost = _ev(c.capacity_mt), _ev(c.capital_cost_lakh)

    def check(name, check_id, value, page, limit, finding_args):
        over = value > limit * (1 + BENCHMARK_TOLERANCE)
        results.append(
            CalculationResult(
                name,
                computed_value=limit,
                stated_value=value,
                match=not over,
                discrepancy=round(value - limit, 2),
                note=norm.key,
            )
        )
        if over:
            findings.append(
                _finding(check_id=check_id, page=page, benchmark=norm, **finding_args)
            )

    if capacity and cost:
        per_mt = round(cost[0] * 100_000 / capacity[0])
        band = norm.cost_per_mt_rupees
        check(
            "benchmark_cost_per_mt",
            "BENCHMARK_STRUCTURE_COST",
            per_mt,
            cost[1],
            band.high,
            dict(
                category="project_cost",
                title="Cold storage cost above the subsidy cost norm",
                explanation=(
                    f"The cold storage costs {cost[0]:g} lakh for {capacity[0]:g} "
                    f"tonnes, i.e. {per_mt:,} rupees per tonne. MIDH/NHB subsidy "
                    f"for a {norm.label} is computed on at most "
                    f"{band.low:,.0f}-{band.high:,.0f} rupees per tonne, i.e. on "
                    f"about {band.low * capacity[0] / 100_000:,.0f}-"
                    f"{band.high * capacity[0] / 100_000:,.0f} lakh of this cost; "
                    "the rest earns no subsidy."
                ),
                action=(
                    "Count subsidy only on the norm cost in the means of finance, "
                    "fund the excess from the promoter or the loan, and support "
                    "the cost with itemised quotations."
                ),
            ),
        )
    volume = _ev(c.chamber_volume_cubic_m)
    if capacity and volume:
        fits = round(volume[0] / CUBIC_M_PER_MT)
        check(
            "benchmark_capacity_vs_volume",
            "BENCHMARK_CAPACITY",
            capacity[0],
            capacity[1],
            fits,
            dict(
                category="assumption",
                title="Stated capacity larger than the chambers hold",
                explanation=(
                    f"The chambers total {volume[0]:g} m3, which holds about "
                    f"{fits:g} tonnes at the norm of {CUBIC_M_PER_MT:g} m3 per "
                    f"tonne; the DPR states {capacity[0]:g} tonnes."
                ),
                action=(
                    "State the capacity from the chamber volume at 3.4 m3 per "
                    "tonne, and base the rent income on it."
                ),
            ),
        )
    rent = _ev(c.rent_per_quintal_rupees)
    seasonal = (c.rent_basis or "season").casefold().startswith("season")
    potato = "potato" in (c.commodity or "").casefold()
    if rent and seasonal and potato:
        band = POTATO_RENT_PER_QUINTAL_SEASON
        check(
            "benchmark_potato_rent",
            "BENCHMARK_RATE",
            rent[0],
            rent[1],
            band.high,
            dict(
                category="market",
                title="Storage rent above the market range",
                explanation=(
                    f"The DPR charges {rent[0]:g} rupees a quintal a season for "
                    f"potato; rents are {band.low:g}-{band.high:g} rupees a "
                    "quintal a season, and some states fix them."
                ),
                action=(
                    "Use the rent fixed or prevailing in the district, with a source."
                ),
            ),
        )

    annexure = {
        "Cold storage (read from the DPR)": " ".join(
            x
            for x in (
                c.storage_type,
                f"{capacity[0]:g} MT" if capacity else None,
                f"for {c.commodity}" if c.commodity else None,
            )
            if x
        )
        or "not stated",
        "Benchmark used (cold storage)": (
            f"{norm.label}"
            + ("" if known else " (type not stated; the higher norm)")
            + f": {norm.cost_per_mt_rupees.low:,.0f}-"
            f"{norm.cost_per_mt_rupees.high:,.0f} rupees/MT, "
            f"{CUBIC_M_PER_MT:g} m3 per MT"
            + (
                f", potato rent {POTATO_RENT_PER_QUINTAL_SEASON.low:g}-"
                f"{POTATO_RENT_PER_QUINTAL_SEASON.high:g} rupees/quintal/season"
                if potato
                else ""
            )
            + f" ({_status_text(norm)})"
        ),
        "Benchmark basis (cold storage)": norm.basis,
    }
    return (
        results,
        findings,
        {
            "sector": "cold_storage",
            "benchmark": norm.key,
            "annexure": annexure,
            "reference": _references(
                norm,
                price=(
                    rent[0] if rent and seasonal and potato else None,
                    POTATO_RENT_PER_QUINTAL_SEASON,
                    "quintal a season",
                ),
            ),
        },
    )


# Units for the action plan's "your DPR" / "should be" columns.
_UNITS = {
    "benchmark_acres_per_day": " acres/day",
    "benchmark_acres_per_year": " acres/year",
    "benchmark_rate_per_acre": " ₹/acre",
    "benchmark_structure_cost_per_sq_m": " ₹/m²",
    "benchmark_yield_per_sq_m": " /m²/year",
    "benchmark_price_per_unit": " ₹/unit",
    "benchmark_milk_yield_per_day": " L/day",
    "benchmark_lactation_days": " days",
    "benchmark_milk_per_animal_per_year": " L/year",
    "benchmark_milk_price": " ₹/L",
    "benchmark_cost_per_mt": " ₹/MT",
    "benchmark_capacity_vs_volume": " m³/MT",
    "benchmark_potato_rent": " ₹/quintal",
}
_WHY_BENCHMARK = "Figures beyond the norm make projections look optimistic"


def _with_values(run):
    """Each finding follows its failing calculation, in order: copy the
    DPR's figure and the benchmark limit onto it. Left alone if the two
    lists do not pair up."""
    results, findings, record = run
    failed = [r for r in results if not r.match]
    if len(failed) == len(findings):
        for r, f in zip(failed, findings, strict=True):
            unit = _UNITS.get(r.check, "")
            f.why_it_matters = f.why_it_matters or _WHY_BENCHMARK
            if r.stated_value is not None:
                f.dpr_stated_value = f"{r.stated_value:,g}{unit}"
            if r.computed_value is not None:
                f.should_be = f"≤ {r.computed_value:,g}{unit}"
    else:
        for f in findings:
            f.why_it_matters = f.why_it_matters or _WHY_BENCHMARK
    return run


def run_benchmark_checks(
    fin: ProjectFinancials, pages: list, *, acreage_flagged: bool = False
) -> tuple[list[CalculationResult], list[Finding], dict | None]:
    """(calculations, findings, record) against the fixed table for each
    sector the DPR belongs to; nothing for sectors the table does not
    cover. `acreage_flagged`: see _harvester_checks."""
    runs = _benchmark_runs(fin, pages, acreage_flagged=acreage_flagged)
    if not runs:
        return [], [], None
    runs = [_with_values(r) for r in runs]
    if len(runs) == 1:
        return runs[0]
    return _merge_runs(runs)


def _benchmark_runs(fin, pages, *, acreage_flagged):
    runs = []
    if is_harvester(fin):
        runs.append(_harvester_checks(fin, pages, acreage_flagged=acreage_flagged))
    if is_polyhouse(fin):
        runs.append(_polyhouse_checks(fin, pages))
    if is_dairy(fin):
        runs.append(_dairy_checks(fin, pages))
    if is_cold_storage(fin):
        runs.append(_cold_storage_checks(fin, pages))
    return runs


def _merge_runs(runs):
    record = {
        "sector": "+".join(r[2]["sector"] for r in runs if r[2].get("sector")),
        "benchmark": "+".join(r[2]["benchmark"] for r in runs),
        "annexure": {k: v for r in runs for k, v in r[2]["annexure"].items()},
        # The first sector's figure wins where two give the same item.
        "reference": {
            k: v for r in reversed(runs) for k, v in r[2].get("reference", {}).items()
        },
    }
    return (
        [x for r in runs for x in r[0]],
        [x for r in runs for x in r[1]],
        record,
    )


def covered_by_table(fin: ProjectFinancials | None) -> bool:
    """True when the fixed table, not web search, benchmarks this DPR."""
    return any(
        check(fin) for check in (is_harvester, is_polyhouse, is_dairy, is_cold_storage)
    )
