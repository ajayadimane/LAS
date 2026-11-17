"""Policy and Research agents: public information, found with Google Search.

One Gemini call per agent: grounded_search -- Gemini with the google_search
tool answers a brief and returns groundingMetadata, which web sources
support which sentences. (A second, structuring call was tried and dropped:
seen live, it looped to its output cap on research answers.)

Code then decides what is kept (Gemini never does):
- Statements are the sentences Google attributes to at least one web
  source; nothing unattributed is kept.
- Benchmarks come from fixed "BENCHMARK: metric = low-high unit" lines at
  the end of the research answer, kept only when both figures appear in a
  source-attributed sentence (whose sources they carry).
- Each source is tiered by domain: "official" (government, RBI, NABARD, the
  banks' own sites) or "secondary". A policy statement from secondary
  sources only is kept as context, labelled so.
- Statements asserting eligibility, approval or sanction are dropped: a
  search result cannot certify that.

PRIVACY (hard rule): the search brief is built here, by code, from a fixed
set of non-identifying fields -- lending bank, scheme claimed, sector,
state (never village, district or address), a loan-size band and the DPR's
own operating figures. Never the borrower's name, mobile, address, PAN or
the DPR's text. Borrower-name words are also stripped from every free-text
field, defensively.

Results are cached by the brief (FileGroundingCache for the evaluation,
PostgresGroundingCache for the worker, 30-day TTL by default,
DPR_GROUNDING_CACHE_TTL_DAYS): the same project gets the same evidence
within the window, and search cost is paid once.

Benchmarks: search figures are market evidence (Annexure D). They become
a finding (rate per unit above the published range, should-fix, citing its
sources) only in sectors the fixed benchmark table does not cover; harvester
DPRs are checked against that table, by machine class (benchmarks.py).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .financial_schema import ExtractedValue, ProjectFinancials
from .finding_rules import compute_dedup_key
from .schemas import (
    AgentOutput,
    DocumentFact,
    ExternalSource,
    ExternalStatement,
    Finding,
)

logger = logging.getLogger(__name__)

INDIAN_STATES = (
    "Andhra Pradesh",
    "Arunachal Pradesh",
    "Assam",
    "Bihar",
    "Chhattisgarh",
    "Goa",
    "Gujarat",
    "Haryana",
    "Himachal Pradesh",
    "Jharkhand",
    "Karnataka",
    "Kerala",
    "Madhya Pradesh",
    "Maharashtra",
    "Manipur",
    "Meghalaya",
    "Mizoram",
    "Nagaland",
    "Odisha",
    "Punjab",
    "Rajasthan",
    "Sikkim",
    "Tamil Nadu",
    "Telangana",
    "Tripura",
    "Uttar Pradesh",
    "Uttarakhand",
    "West Bengal",
    "Delhi",
    "Jammu and Kashmir",
    "Ladakh",
    "Puducherry",
    "Chandigarh",
)
# Abbreviations DPRs print in addresses ("REWA (MP)").
_STATE_ABBREVIATIONS = {
    "MP": "Madhya Pradesh",
    "UP": "Uttar Pradesh",
    "AP": "Andhra Pradesh",
    "HP": "Himachal Pradesh",
    "TN": "Tamil Nadu",
    "WB": "West Bengal",
}

OFFICIAL_DOMAINS = (
    "rbi.org.in",
    "nabard.org",
    "sidbi.in",
    "sbi.co.in",
    "bankofbaroda.in",
    "canarabank.com",
    "pnbindia.in",
    "unionbankofindia.co.in",
    "bankofindia.co.in",
    "indianbank.in",
    "centralbankofindia.co.in",
    "iob.in",
    "ucobank.com",
    "bankofmaharashtra.in",
    "psbindia.com",
    "mudra.org.in",
    "cgtmse.in",
    "agriinfra.dac.gov.in",
)
# ".bank.in": the domain RBI requires Indian banks to move to (seen live:
# bankofmaharashtra.bank.in).
# ".ac.in" / ".res.in": agricultural universities and ICAR institutes,
# the authoritative source for farm-machinery benchmarks (seen live:
# tnau.ac.in for annual combine coverage).
_OFFICIAL_SUFFIXES = (".gov.in", ".nic.in", ".bank.in", ".ac.in", ".res.in")
# Canonical names: the brief (and so the cache key) must not change with
# how a DPR or a run words the same scheme or bank (seen live: "AGRI INFRA"
# vs "Agriculture Infrastructure Fund AIF"; sector present in one run only).
# Unrecognised schemes are left out rather than sent as free text.
SCHEMES = (
    (
        "Agriculture Infrastructure Fund (AIF)",
        re.compile(r"\baif\b|agri(?:culture)?\.?\s*infra", re.I),
    ),
    (
        "SMAM (Sub-Mission on Agricultural Mechanization)",
        re.compile(r"\bsmam\b|sub[- ]?mission on agri\w* mechani", re.I),
    ),
    ("Custom Hiring Centre scheme", re.compile(r"custom\s+hiring|\bchc\b", re.I)),
    (
        "PMEGP",
        re.compile(r"\bpmegp\b|prime minister.{0,20}employment generation", re.I),
    ),
    ("PMFME", re.compile(r"\bpmfme\b|formali[sz]ation of micro food", re.I)),
    ("CGTMSE", re.compile(r"cgtmse|credit guarantee fund trust", re.I)),
    ("NABARD refinance", re.compile(r"nabard", re.I)),
)
BANKS = (
    ("State Bank of India", re.compile(r"state bank of india|\bsbi\b", re.I)),
    ("Bank of Baroda", re.compile(r"bank of baroda|\bbob\b", re.I)),
    ("Canara Bank", re.compile(r"canara", re.I)),
    ("Punjab National Bank", re.compile(r"punjab national|\bpnb\b", re.I)),
    ("Union Bank of India", re.compile(r"union bank", re.I)),
    ("Bank of India", re.compile(r"^\s*bank of india\b|\bboi\b", re.I)),
    ("Indian Bank", re.compile(r"^\s*indian bank\b", re.I)),
    ("Central Bank of India", re.compile(r"central bank of india", re.I)),
    ("Indian Overseas Bank", re.compile(r"indian overseas|\biob\b", re.I)),
    ("UCO Bank", re.compile(r"\buco\b", re.I)),
    ("Bank of Maharashtra", re.compile(r"bank of maharashtra", re.I)),
    ("Punjab & Sind Bank", re.compile(r"punjab (?:&|and) sind", re.I)),
    ("HDFC Bank", re.compile(r"\bhdfc\b", re.I)),
    ("ICICI Bank", re.compile(r"\bicici\b", re.I)),
    ("Axis Bank", re.compile(r"\baxis\b", re.I)),
)


def canonical_schemes(text: str | None) -> str | None:
    if not text:
        return None
    names = [name for name, pattern in SCHEMES if pattern.search(text)]
    return "; ".join(names) or None


def canonical_bank(text: str | None, banned: set[str]) -> str | None:
    if not text:
        return None
    for name, pattern in BANKS:
        if pattern.search(text):
            return name
    return _clean(text, banned)


def verified_dpr_bank(facts) -> str | None:
    """The bank the DPR is addressed to: its verified bank_name facts, only
    when they all agree (a contradictory DPR names no bank)."""
    values = {
        f.value.strip()
        for f in facts
        if f.field == "bank_name" and f.verified and f.value and f.value.strip()
    }
    if len({v.casefold() for v in values}) != 1:
        return None
    return next(iter(values))


def resolve_lending_bank(
    facts, project_context: dict | None, default: str | None = None
) -> tuple[str | None, str | None]:
    """(bank, source) for the analysis: the DPR's own verified bank first,
    then the bank given on the dashboard (BankName), then the configured
    default (DPR_DEFAULT_LENDING_BANK, State Bank of India). source is
    "dpr", "dashboard" or "default"; (None, None) only if the default is
    configured empty. The report and the policy search both use this, so
    they never name different banks."""
    if bank := verified_dpr_bank(facts):
        return bank, "dpr"
    given = ((project_context or {}).get("bank_name") or "").strip()
    if given and given.casefold() != "na":
        return given, "dashboard"
    if default is None:
        from app.core.config import get_settings

        default = get_settings().dpr_default_lending_bank
    default = (default or "").strip()
    return (default, "default") if default else (None, None)


def lending_bank_conflict(facts, project_context: dict | None) -> str | None:
    """A clarification when the DPR and the dashboard name different banks
    (the DPR's wins); None when they agree or either is missing."""
    dpr = verified_dpr_bank(facts)
    given = ((project_context or {}).get("bank_name") or "").strip()
    if not dpr or not given or given.casefold() == "na":
        return None
    if canonical_bank(dpr, set()) == canonical_bank(given, set()):
        return None
    return (
        f"BANK_MISMATCH: the DPR is addressed to {dpr}, but {given} was "
        f"chosen on the dashboard. This analysis uses {dpr}; confirm the "
        "lender."
    )


_VERDICT = re.compile(
    r"\b(?:is|are|will be|would be)\s+(?:fully\s+)?eligible\b|\bapproved\b|"
    r"\bsanctioned\b|\bguaranteed approval\b",
    re.I,
)
METRICS = (
    "capacity_per_day",
    "operating_days_per_season",
    "seasons_per_year",
    "operating_days_per_year",
    "price_per_unit_rupees",
    "variable_cost_per_unit_rupees",
    "fuel_litres_per_unit",
    "fuel_price_per_litre",
    "other",
)
# Part of every cache key: bump when the search prompts or the parsing
# change, so cached results from the old design are not reused.
# 2: one grounded call, statements = supported sentences, BENCHMARK lines.
# 3: research searches with its own brief (RESEARCH_KEYS).
GROUNDING_VERSION = 3
# Statements kept per search. Seen live: unbounded, the structuring call
# looped to its output cap (16k tokens) on research answers.
MAX_STATEMENTS = 15
# A DPR figure this far outside the benchmark range is flagged.
BENCHMARK_TOLERANCE = 0.10


def loan_band(amount_lakh: float | None) -> str | None:
    if amount_lakh is None:
        return None
    for limit, label in (
        (10, "up to 10 lakh"),
        (25, "10-25 lakh"),
        (50, "25-50 lakh"),
        (100, "50 lakh-1 crore"),
    ):
        if amount_lakh <= limit:
            return label
    return "above 1 crore"


def state_of(location: str | None) -> str | None:
    """The state named in a location string, or None -- never the
    location itself (a village or address identifies the borrower)."""
    if not location:
        return None
    folded = location.casefold()
    for state in INDIAN_STATES:
        if state.casefold() in folded:
            return state
    for abbreviation, state in _STATE_ABBREVIATIONS.items():
        if re.search(rf"\(\s*{abbreviation}\s*\)|\b{abbreviation}\b\s*$", location):
            return state
    return None


def _clean(value: str | None, banned: set[str], limit: int = 80) -> str | None:
    """Free text minus any borrower-name word and any digit run."""
    if not value:
        return None
    words = [
        w
        for w in re.findall(r"[A-Za-z][A-Za-z&.\-]*", value)
        if w.casefold() not in banned
    ]
    text = " ".join(words)[:limit].strip()
    return text or None


def _fact(facts: list[DocumentFact], field: str) -> str | None:
    return next((f.value for f in facts if f.field == field), None)


def _ev(value: ExtractedValue | None) -> float | None:
    if value is None or value.status != "extracted":
        return None
    return value.value


def build_search_brief(
    facts: list[DocumentFact],
    fin: ProjectFinancials | None,
    project_context: dict,
) -> dict:
    """The only data that leaves for a web search. Keys with no value are
    omitted. See the module docstring for the privacy rule."""
    banned = {
        w.casefold()
        for w in re.findall(r"[A-Za-z]{3,}", _fact(facts, "borrower_name") or "")
    }
    brief = {
        "bank": canonical_bank(resolve_lending_bank(facts, project_context)[0], banned),
        "scheme_claimed": canonical_schemes(_fact(facts, "scheme_claimed")),
        "sector": _clean(_fact(facts, "business_sector"), banned),
        "subsector": _clean(_fact(facts, "business_subsector"), banned),
        "state": state_of(_fact(facts, "project_location")),
        "loan_band": loan_band(_ev(fin.term_loan_amount) if fin else None),
    }
    if fin is not None and fin.harvester_assumptions is not None:
        # Known from the extraction, and the same every run: a combine-
        # harvester DPR (seen live: the document agent's sector and
        # subsector wording varied, or was missing, run to run).
        brief["sector"] = "agricultural machinery custom hiring"
        brief["subsector"] = "combine harvester custom hiring"
    operating: dict[str, object] = {}
    if fin is not None and fin.unit_economics is not None:
        ue = fin.unit_economics
        if ue.unit_name:
            operating["unit"] = _clean(ue.unit_name, banned, 30)
        for key in (
            "capacity_per_day",
            "operating_days_per_year",
            "capacity_per_year",
            "price_per_unit_rupees",
            "variable_cost_per_unit_rupees",
        ):
            if (v := _ev(getattr(ue, key))) is not None:
                operating[key] = v
    if fin is not None and fin.harvester_assumptions is not None:
        h = fin.harvester_assumptions
        operating.setdefault("unit", "acre")
        for key, src in (
            ("acres_per_year", h.acres_per_year),
            ("price_per_unit_rupees", h.price_per_acre),
            ("fuel_price_per_litre", h.fuel_price_per_litre),
        ):
            if (v := _ev(src)) is not None:
                operating.setdefault(key, v)
    if operating:
        brief["dpr_operating_figures"] = operating
    return {k: v for k, v in brief.items() if v}


# What each search needs. Market benchmarks do not depend on the bank,
# scheme or loan size; seen live, including them gave two DPRs for the same
# machine different benchmark sets (12-25 vs 8-15 acres/day), so the same
# 1200-acre plan was flagged on one and not the other.
RESEARCH_KEYS = ("sector", "subsector", "state", "dpr_operating_figures")


def brief_for(kind: str, brief: dict) -> dict:
    if kind == "research":
        return {k: v for k, v in brief.items() if k in RESEARCH_KEYS}
    return brief


def cache_key(kind: str, brief: dict) -> str:
    """Stable per (kind, brief). Operating figures are left out: the
    evidence sought is about the sector and scheme, not this DPR's numbers."""
    basis = {k: v for k, v in brief.items() if k != "dpr_operating_figures"}
    basis = {k: str(v).casefold() for k, v in basis.items()}
    raw = json.dumps({"kind": kind, "v": GROUNDING_VERSION, **basis}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def tier_of(domain: str) -> str:
    d = domain.casefold().removeprefix("www.")
    if d.endswith(_OFFICIAL_SUFFIXES) or any(
        d == o or d.endswith("." + o) for o in OFFICIAL_DOMAINS
    ):
        return "official"
    return "secondary"


def supported_segments(grounding: dict) -> list[dict]:
    """[{id, text, sources: [{url, domain, tier}]}] from groundingMetadata:
    only text segments Google attributes to at least one web source."""
    chunks = []
    for chunk in grounding.get("groundingChunks") or []:
        web = chunk.get("web") or {}
        # The title is the source domain; the uri is Google's redirect.
        domain = (web.get("title") or "").strip()
        chunks.append(
            {"url": web.get("uri"), "domain": domain, "tier": tier_of(domain)}
        )
    out = []
    for i, support in enumerate(grounding.get("groundingSupports") or [], start=1):
        sources = [
            chunks[c]
            for c in support.get("groundingChunkIndices") or []
            if 0 <= c < len(chunks) and chunks[c]["url"]
        ]
        text = ((support.get("segment") or {}).get("text") or "").strip()
        if sources and text:
            out.append({"id": f"S{i}", "text": text, "sources": sources})
    return out


SEARCH_PROMPTS = {
    "policy": (
        "You are an Indian bank lending-policy researcher. Using Google "
        "Search, find current, public, authoritative information relevant to "
        "a term-loan application described by the brief: the lending bank's "
        "published product terms for this sector (margin, tenor, "
        "moratorium, collateral, DSCR expectations, required documents) and "
        "the conditions of the scheme claimed (subsidy rate and caps, "
        "interest subvention, credit guarantee, who is covered). Prefer the "
        "bank's own site, RBI, NABARD and government scheme portals. State "
        "each condition plainly with figures; never state that this "
        "applicant is eligible, approved or sanctioned."
    ),
    "research": (
        "You are an agricultural and MSME market researcher. Using Google "
        "Search, find current public benchmarks for the project in the brief, "
        "in its state where possible: typical capacity per day of the main "
        "machine or plant, operating days per season and seasons per year, "
        "the going rate charged per unit, running cost per unit, fuel use "
        "per unit and fuel price. Give ranges with units, and compare them "
        "with 'dpr_operating_figures' where given. End the answer with one "
        "line per benchmark you found, exactly in this form: "
        "'BENCHMARK: <metric> = <low>-<high> <unit>', where <metric> is one "
        "of " + ", ".join(m for m in METRICS if m != "other") + " (for a "
        "single figure, low and high are the same)."
    ),
}
_BENCHMARK_LINE = re.compile(
    r"BENCHMARK:\s*([a-z_]+)\s*=\s*([\d.,]+)\s*(?:-|–|to)?\s*([\d.,]*)\s*([^\n]*)",
    re.I,
)


def _sources_of(segments: list[dict]) -> list[ExternalSource]:
    sources, seen = [], set()
    for segment in segments:
        for source in segment["sources"]:
            if source["url"] not in seen:
                seen.add(source["url"])
                sources.append(ExternalSource(**source))
    return sources


def _number(token: str) -> float | None:
    try:
        return float(token.replace(",", "")) if token else None
    except ValueError:
        return None


def _mentions(text: str, value: float) -> bool:
    flat = text.replace(",", "")
    forms = {f"{value:g}", f"{value:.1f}", f"{value:.2f}"}
    if value.is_integer():
        forms.add(str(int(value)))
    return any(re.search(rf"(?<![\d.]){re.escape(f)}(?![\d])", flat) for f in forms)


def _plain(text: str) -> str:
    """Markdown table rows and emphasis as plain text (seen live:
    "| **Machine Capacity per Day** | **12 - 25 acres/day** |")."""
    text = re.sub(r"<br\s*/?>", "; ", text)
    text = text.replace("**", "").replace("__", "")
    cells = [c.strip() for c in text.strip().strip("|").split("|")]
    return ": ".join(c for c in cells if c)


def statements_from_segments(
    kind: str, segments: list[dict]
) -> list[ExternalStatement]:
    """Each source-supported sentence is a statement (Google already split
    the answer into sentences and attributed each). Official-sourced first,
    then in answer order; eligibility/approval wording dropped; at most
    MAX_STATEMENTS. BENCHMARK lines are not statements (see benchmarks_from)."""
    now = datetime.now(timezone.utc)
    out = []
    for segment in segments:
        text = _plain(segment["text"])
        if not text or _VERDICT.search(text) or _BENCHMARK_LINE.search(text):
            continue
        sources = _sources_of([segment])
        out.append(
            ExternalStatement(
                kind=kind,
                text=text[:600],
                sources=sources,
                official=any(x.tier == "official" for x in sources),
                retrieved_at=now,
            )
        )
    out.sort(key=lambda st: not st.official)  # stable: answer order kept
    return out[:MAX_STATEMENTS]


def benchmarks_from(answer: str, segments: list[dict]) -> list[ExternalStatement]:
    """The answer's BENCHMARK lines, kept only when their figures appear in
    a source-supported sentence -- whose sources they then carry. A figure
    no source supports is dropped (seen live, before this: "capacity 0.0")."""
    now = datetime.now(timezone.utc)
    out = []
    for match in _BENCHMARK_LINE.finditer(answer):
        metric = match.group(1).casefold()
        low = _number(match.group(2))
        high = _number(match.group(3)) or low
        if metric not in METRICS or metric == "other" or not low or not high:
            continue
        low, high = min(low, high), max(low, high)
        backing = [
            s
            for s in segments
            if _mentions(s["text"], low) and _mentions(s["text"], high)
        ]
        if not backing:
            continue
        sources = _sources_of(backing)
        out.append(
            ExternalStatement(
                kind="research",
                text=f"{metric.replace('_', ' ')}: {low:g}-{high:g} "
                f"{match.group(4).strip()}".strip()[:200],
                metric=metric,
                value_low=low,
                value_high=high,
                unit=match.group(4).strip()[:40] or None,
                sources=sources,
                official=any(x.tier == "official" for x in sources),
                retrieved_at=now,
            )
        )
    return out


class GroundingCache(Protocol):
    def get(self, key: str) -> list[dict] | None: ...

    def put(self, key: str, kind: str, statements: list[dict]) -> None: ...


class FileGroundingCache:
    """Evaluation only (eval/grounding_cache/, gitignored)."""

    def __init__(self, directory: Path):
        self.directory = directory

    def get(self, key):
        path = self.directory / f"{key}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def put(self, key, kind, statements):
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / f"{key}.json").write_text(json.dumps(statements))


class PostgresGroundingCache:
    """Table dpr_grounding_cache (migration 012). Public web evidence keyed
    by a hash of the non-identifying brief; not document content."""

    def __init__(self, engine, ttl_days: int):
        self.engine, self.ttl_days = engine, ttl_days

    def get(self, key):
        from sqlalchemy import text

        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT statements FROM dpr_grounding_cache "
                    "WHERE cache_key=:k AND expires_at > now()"
                ),
                {"k": key},
            ).first()
        if row is None:
            return None
        value = row[0]
        return json.loads(value) if isinstance(value, str) else value

    def put(self, key, kind, statements):
        from sqlalchemy import text

        with self.engine.begin() as conn:
            conn.execute(
                text("""
                INSERT INTO dpr_grounding_cache
                    (cache_key, kind, statements, expires_at)
                VALUES (:k, :kind, CAST(:s AS jsonb),
                        now() + make_interval(days => :ttl))
                ON CONFLICT (cache_key) DO UPDATE
                  SET statements = EXCLUDED.statements, expires_at = EXCLUDED.expires_at
            """),
                {
                    "k": key,
                    "kind": kind,
                    "s": json.dumps(statements),
                    "ttl": self.ttl_days,
                },
            )


def purge_expired_grounding_cache(engine) -> int:
    from sqlalchemy import text

    with engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM dpr_grounding_cache WHERE expires_at <= now()")
        )
    return result.rowcount or 0


async def run_grounded_role(
    llm, kind: str, brief: dict, cache: GroundingCache | None
) -> list[ExternalStatement]:
    """Cached statements for (kind, brief), else one grounded Gemini call.
    Raises GeminiAgentError on failure (the caller degrades the role)."""
    from .gemini import CURRENT_ROLE

    brief = brief_for(kind, brief)
    key = cache_key(kind, brief)
    if cache is not None:
        try:
            if (hit := cache.get(key)) is not None:
                logger.info("dpr_grounding_cache_hit kind=%s", kind)
                return [ExternalStatement.model_validate(s) for s in hit]
        except Exception:
            logger.warning("dpr_grounding_cache_read_failed", exc_info=True)
    token = CURRENT_ROLE.set(kind)
    try:
        answer = await llm.grounded_search(SEARCH_PROMPTS[kind], brief)
    finally:
        CURRENT_ROLE.reset(token)
    segments = supported_segments(answer.get("grounding") or {})
    statements = statements_from_segments(kind, segments)
    if kind == "research":
        statements += benchmarks_from(answer.get("text") or "", segments)
    logger.info(
        "dpr_grounding kind=%s segments=%d statements=%d official=%d",
        kind,
        len(segments),
        len(statements),
        sum(1 for s in statements if s.official),
    )
    if cache is not None and statements:
        try:
            cache.put(key, kind, [s.model_dump(mode="json") for s in statements])
        except Exception:
            logger.warning("dpr_grounding_cache_write_failed", exc_info=True)
    return statements


def _searched_for(brief: dict) -> str:
    return ", ".join(
        f"{k}={v}" for k, v in brief.items() if k != "dpr_operating_figures"
    )


def role_output(
    kind: str, statements: list[ExternalStatement], brief: dict
) -> AgentOutput:
    """The agent's output for the graph: a summary other agents read, and
    a question for the borrower or bank per official policy condition."""
    if not statements:
        return AgentOutput(
            summary=f"No sourced public {kind} information was found for this brief.",
            unresolved_questions=[
                "Verify current market references and the bank's product "
                "requirements with authoritative sources."
            ]
            if kind == "policy"
            else [],
        )
    lines = [
        f"- {s.text} [{', '.join(sorted({src.domain for src in s.sources}))}"
        f"{'; official' if s.official else ''}]"
        for s in statements[:12]
    ]
    questions = [
        f"POLICY_CONFIRMATION: {s.text} -- confirm with the bank that this "
        "applies and that the DPR meets it."
        for s in statements
        if kind == "policy" and s.official
    ][:4]
    return AgentOutput(
        summary=f"Public {kind} information (searched for: "
        f"{_searched_for(brief_for(kind, brief))}):\n" + "\n".join(lines),
        unresolved_questions=questions,
    )


def _benchmark(
    statements: list[ExternalStatement], metric: str
) -> ExternalStatement | None:
    """The benchmark for `metric` with the most sources (official first)."""
    candidates = [
        s
        for s in statements
        if s.metric == metric and s.value_low is not None and s.value_high is not None
    ]
    return max(candidates, key=lambda s: (s.official, len(s.sources)), default=None)


def _sources_text(*statements: ExternalStatement) -> str:
    domains = sorted({src.domain for s in statements for src in s.sources})
    return ", ".join(domains)


def benchmark_findings(
    statements: list[ExternalStatement], fin: ProjectFinancials | None
) -> list[Finding]:
    """DPR figures checked against web-search benchmarks -- only in sectors
    the fixed benchmark table does not cover. A harvester DPR is checked
    against the table for its machine class (benchmarks.py) instead: seen
    live, search returned a different machine class per DPR, so the same
    plan was flagged on one DPR and not another. Should-fix at most."""
    from .benchmarks import covered_by_table

    if fin is None or covered_by_table(fin):
        return []
    ue = fin.unit_economics
    price = _ev(ue.price_per_unit_rupees) if ue is not None else None
    rate = _benchmark(statements, "price_per_unit_rupees")
    if not (price and rate and price > rate.value_high * (1 + BENCHMARK_TOLERANCE)):
        return []
    f = Finding(
        category="market",
        severity="medium",
        title="Rate per unit above the published market range",
        explanation=(
            f"The DPR charges {price:g} rupees per unit; published rates are "
            f"{rate.value_low:g}-{rate.value_high:g} {rate.unit or ''}".rstrip()
            + f". Sources: {_sources_text(rate)}."
        ),
        evidence_page=ue.price_per_unit_rupees.page,
        suggested_action=(
            "Support the rate with local quotations or lower it to the market range."
        ),
        provenance="ai_interpreted",
        verification="unverified",
        # Anchored like a checklist item: its rating is fixed here, so the
        # verifier cannot downgrade it (seen live: downgraded to good-to-fix).
        check_id="BENCHMARK_RATE",
    )
    f.priority = "should_fix"
    f.priority_rule = (
        "SHOULD_FIX: DPR figure outside an external benchmark "
        "(grounding.py); external evidence is capped at should-fix."
    )
    f.finding_class = "business_risk"
    f.dedup_key = compute_dedup_key(f)
    return [f]
