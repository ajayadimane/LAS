"""Scoring for the DPR evaluation harness (scripts/dpr_eval.py).

Pure functions over an AnalysisResult and a hand-checked annotation -- no
network, no database -- so the scoring itself is unit-tested offline
(tests/test_dpr_evaluation.py) and the harness only adds the live run.

Annotation format (eval/annotations/<doc-id>.yaml, keyed by the document's
sha256, never by the borrower's name):

    document_sha256: <64 hex>
    label: approved | rejected        # the bank's real decision
    reviewed: false                   # true once a credit person checked it
    expected:                         # figures as stated in the DPR, INR lakh
      cost_of_project_stated_total: 30.0
      term_loan_amount: 22.5
      years:
        1: {net_profit: 4.27, principal_repayment: 3.21}
    must_find:                        # issues a good review must raise
      - id: fuel_cost
        any_keywords: [fuel]          # any of these in title/explanation
        categories: [financial_calculation]   # optional
        page: 8                       # optional
    must_not_find:                    # claims that would be wrong
      - id: no_quotation_claim
        any_keywords: [quotation missing]
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

# Figures read straight from ProjectFinancials (scalars or ExtractedValue).
SCALAR_FIELDS = (
    "cost_of_project_stated_total",
    "means_of_finance_stated_total",
    "standard_interest_rate_pct",
    "subvention_rate_pct",
    "effective_interest_rate_pct",
    "term_loan_amount",
    "loan_tenor_years",
    "loan_moratorium_years",
    "fund_flow_sources_total",
    "fund_flow_applications_total",
)
YEAR_FIELDS = (
    "revenue",
    "operating_expenses",
    "net_profit",
    "depreciation",
    "interest_on_term_loan",
    "principal_repayment",
    "interest_repayment",
    "stated_dscr",
    "profit_before_tax",
    "tax_paid",
)

# A figure matches when within 0.5% or 0.01 absolute (lakh rounding).
REL_TOLERANCE = 0.005
ABS_TOLERANCE = 0.01

# Indicative prices, USD per 1M tokens (input, output; thinking bills as
# output) and per grounded search request. Third-party sources disagree, so
# verify against Google's pricing page; override with --pricing-json.
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "gemini-3.8-flash": {"input": 0.75, "output": 3.75},
    "gemini-3.1-pro": {"input": 2.00, "output": 12.00},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
}
DEFAULT_USD_INR = 88.0
# Google Search grounding, USD per search query (indicative; check Google's
# pricing page -- a monthly free allowance may apply).
SEARCH_USD_PER_QUERY = 0.014


# --------------------------------------------------------------------------
# Extraction accuracy
# --------------------------------------------------------------------------


def _value(raw: Any) -> float | None:
    """A ProjectFinancials field as dumped to JSON: a bare number, or an
    ExtractedValue dict that only counts when status == "extracted"."""
    if isinstance(raw, dict):
        if raw.get("status") != "extracted":
            return None
        raw = raw.get("value")
    return None if raw is None else float(raw)


def figures_match(expected: float, actual: float | None) -> bool:
    if actual is None:
        return False
    return abs(actual - expected) <= max(ABS_TOLERANCE, abs(expected) * REL_TOLERANCE)


def flatten_figures(financials: dict | None) -> dict[str, float | None]:
    """{"term_loan_amount": 22.5, "years.1.net_profit": 4.27, ...}; a year
    figure falls back to the repayment table the same way financial_calc
    does."""
    fin = financials or {}
    out: dict[str, float | None] = {k: _value(fin.get(k)) for k in SCALAR_FIELDS}
    table = {r.get("year"): r for r in fin.get("repayment_table") or []}
    for y in fin.get("years") or []:
        year = y.get("year")
        for k in YEAR_FIELDS:
            out[f"years.{year}.{k}"] = _value(y.get(k))
        row = table.get(year) or {}
        if out[f"years.{year}.principal_repayment"] is None:
            out[f"years.{year}.principal_repayment"] = _value(row.get("principal_due"))
        if out[f"years.{year}.interest_repayment"] is None:
            out[f"years.{year}.interest_repayment"] = _value(row.get("interest_due"))
    return out


def flatten_expected(expected: dict | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in (expected or {}).items():
        if key == "years":
            for year, fields in (value or {}).items():
                for k, v in (fields or {}).items():
                    out[f"years.{int(year)}.{k}"] = float(v)
        elif value is not None:
            out[key] = float(value)
    return out


@dataclass
class FieldScore:
    field: str
    expected: float
    actual: float | None
    ok: bool


def score_extraction(
    expected: dict | None, financials: dict | None
) -> list[FieldScore]:
    actual = flatten_figures(financials)
    return [
        FieldScore(k, v, actual.get(k), figures_match(v, actual.get(k)))
        for k, v in sorted(flatten_expected(expected).items())
    ]


# --------------------------------------------------------------------------
# Findings: must-find recall, must-not-find violations
# --------------------------------------------------------------------------


def _text(finding: dict) -> str:
    return f"{finding.get('title', '')} {finding.get('explanation', '')}".casefold()


def finding_matches(spec: dict, finding: dict) -> bool:
    keywords = [k.casefold() for k in spec.get("any_keywords") or []]
    if keywords and not any(k in _text(finding) for k in keywords):
        return False
    categories = spec.get("categories")
    if categories and finding.get("category") not in categories:
        return False
    page = spec.get("page")
    if page is not None and finding.get("evidence_page") != page:
        return False
    return bool(keywords or categories)


@dataclass
class FindingScore:
    recall: float | None  # None when the annotation lists no must_find
    found: list[str]
    missed: list[str]
    violations: list[str]  # must_not_find ids that were raised


def score_findings(annotation: dict, findings: list[dict]) -> FindingScore:
    must = annotation.get("must_find") or []
    found = [s["id"] for s in must if any(finding_matches(s, f) for f in findings)]
    missed = [s["id"] for s in must if s["id"] not in found]
    violations = [
        s["id"]
        for s in annotation.get("must_not_find") or []
        if any(finding_matches(s, f) for f in findings)
    ]
    return FindingScore(
        recall=(len(found) / len(must)) if must else None,
        found=found,
        missed=missed,
        violations=violations,
    )


# --------------------------------------------------------------------------
# Stability across repeated runs of the same document
# --------------------------------------------------------------------------


def jaccard(a: set, b: set) -> float:
    return 1.0 if not a and not b else len(a & b) / len(a | b)


_STOP = {
    "a", "an", "and", "the", "of", "in", "for", "to", "on", "with", "or",
    "from", "by", "is", "not", "no", "vs", "dpr", "missing", "absence",
}
# Two titles name the same topic when their content words overlap this much.
# Calibrated on real repeat runs: "Missing Vendor Quotation / Pro-Forma
# Invoice for Equipment" vs "Missing Vendor Pro-Forma Invoice / Equipment
# Quotation" must match; "Fuel cost understated" vs "Crew wages understated"
# must not.
TOPIC_MATCH = 0.4


def _words(title: str) -> set[str]:
    import re

    return {w for w in re.findall(r"[a-z0-9]+", title.casefold()) if w not in _STOP}


def topic_agreement(a: list[str], b: list[str]) -> float:
    """Share of findings in two runs that have a same-topic counterpart in
    the other (greedy one-to-one matching on title word overlap). 1.0 when
    both are empty. Exact-title comparison understates agreement badly: the
    model rewords the same issue on every run."""
    if not a and not b:
        return 1.0
    unmatched = [_words(t) for t in b]
    matched = 0
    for title in a:
        words = _words(title)
        best, best_score = None, 0.0
        for k, other in enumerate(unmatched):
            score = jaccard(words, other) if words or other else 0.0
            if score > best_score:
                best, best_score = k, score
        if best is not None and best_score >= TOPIC_MATCH:
            unmatched.pop(best)
            matched += 1
    return round(2 * matched / (len(a) + len(b)), 3)


def stability(results: list[dict]) -> dict:
    """How much repeated runs of one document agree: on finding topics
    (all findings; high/critical only; must-fix only), on exact dedup keys
    (a strict lower bound), and on extracted figures."""
    if len(results) < 2:
        return {"runs": len(results)}

    def titles(r: dict, keep) -> list[str]:
        return [f.get("title", "") for f in r.get("findings", []) if keep(f)]

    pairs = [(i, j) for i in range(len(results)) for j in range(i + 1, len(results))]

    def mean_topic(keep) -> float:
        return round(sum(
            topic_agreement(titles(results[i], keep), titles(results[j], keep))
            for i, j in pairs) / len(pairs), 3)

    keys = [{f.get("dedup_key") for f in r.get("findings", [])} for r in results]
    answers = [checklist_answers(r) for r in results]
    flagged = [{k for k, s in a.items() if s == "issue"} for a in answers]
    figures = [flatten_figures(r.get("extracted_financials")) for r in results]
    names = set().union(*figures)
    same = [n for n in names if len({f.get(n) for f in figures}) == 1]
    return {
        "runs": len(results),
        "topic_agreement": mean_topic(lambda f: True),
        "high_severity_topic_agreement": mean_topic(
            lambda f: f.get("severity") in ("critical", "high")),
        "must_fix_topic_agreement": mean_topic(
            lambda f: f.get("priority") == "must_fix"),
        "finding_key_jaccard": round(
            sum(jaccard(keys[i], keys[j]) for i, j in pairs) / len(pairs), 3),
        "figures_identical_share": round(len(same) / len(names), 3) if names else 1.0,
        # Checklist (checklists.py): same answer to the same item, and the
        # same items flagged as issues. None when no run has answers.
        "checklist_answer_agreement": round(sum(
            answer_agreement(answers[i], answers[j]) for i, j in pairs) / len(pairs), 3)
        if any(answers) else None,
        "checklist_flagged_jaccard": round(sum(
            jaccard(flagged[i], flagged[j]) for i, j in pairs) / len(pairs), 3)
        if any(answers) else None,
    }


def checklist_answers(result: dict) -> dict[str, str]:
    """{"role:CHECK_ID": status} for answers to real checklists.py items
    (a role answering an id it was not asked about is ignored)."""
    from .checklists import CHECKLISTS

    known = {role: {i.id for i in items} for role, items in CHECKLISTS.items()}
    out = {}
    for role, output in (result.get("agent_outputs") or {}).items():
        for a in (output or {}).get("checks", []) or []:
            if a.get("check_id") in known.get(role, ()):
                out.setdefault(f"{role}:{a.get('check_id')}", a.get("status"))
    return out


def answer_agreement(a: dict[str, str], b: dict[str, str]) -> float:
    """Share of items answered in either run whose answers are equal (an
    item answered in only one run counts as a disagreement)."""
    items = set(a) | set(b)
    if not items:
        return 1.0
    return len([k for k in items if a.get(k) == b.get(k)]) / len(items)


# --------------------------------------------------------------------------
# Cost and latency from the per-call usage log (gemini.CURRENT_RUN_USAGE)
# --------------------------------------------------------------------------


def _price(model: str, pricing: dict) -> dict | None:
    if model in pricing:
        return pricing[model]
    # Longest matching prefix, e.g. "gemini-3.8-flash-preview-..." -> 3.8-flash.
    candidates = [m for m in pricing if model.startswith(m)]
    return pricing[max(candidates, key=len)] if candidates else None


def usage_summary(
    usage: list[dict],
    model: str,
    *,
    pricing: dict | None = None,
    usd_inr: float = DEFAULT_USD_INR,
) -> dict:
    pricing = pricing or DEFAULT_PRICING
    price = _price(model, pricing)
    by_role: dict[str, dict] = defaultdict(
        lambda: {"calls": 0, "prompt_tokens": 0, "output_tokens": 0, "duration_ms": 0}
    )
    for u in usage:
        role = by_role[u.get("role") or "unknown"]
        role["calls"] += 1
        role["prompt_tokens"] += u.get("prompt_tokens") or 0
        # Billed output = everything that isn't prompt: visible + thinking.
        role["output_tokens"] += max(
            0, (u.get("total_tokens") or 0) - (u.get("prompt_tokens") or 0)
        )
        role["duration_ms"] += u.get("duration_ms") or 0
    total_in = sum(r["prompt_tokens"] for r in by_role.values())
    total_out = sum(r["output_tokens"] for r in by_role.values())
    searches = sum(u.get("search_queries") or 0 for u in usage)
    cost_inr = None
    if price is not None:
        usd = (total_in * price["input"] + total_out * price["output"]) / 1_000_000
        usd += searches * SEARCH_USD_PER_QUERY
        cost_inr = round(usd * usd_inr, 2)
    return {
        "model": model,
        "calls": len(usage),
        "prompt_tokens": total_in,
        "output_tokens": total_out,
        "search_queries": searches,
        "cost_inr": cost_inr,
        "priced": price is not None,
        "by_role": {k: dict(v) for k, v in sorted(by_role.items())},
    }


# --------------------------------------------------------------------------
# Per-document scorecard and a first-draft annotation
# --------------------------------------------------------------------------


@dataclass
class DocumentScore:
    doc_id: str
    label: str
    reviewed: bool
    readiness: str | None
    must_fix: int
    findings: int
    extraction: list[FieldScore] = field(default_factory=list)
    finding_score: FindingScore | None = None

    @property
    def extraction_accuracy(self) -> float | None:
        if not self.extraction:
            return None
        return sum(f.ok for f in self.extraction) / len(self.extraction)

    @property
    def label_agrees(self) -> bool | None:
        """Weak signal against the bank's real decision: a rejected DPR
        should have at least one must-fix issue. An approved DPR may still
        carry fixable issues, so it is not scored."""
        if self.label != "rejected":
            return None
        return self.must_fix > 0


def score_document(
    doc_id: str,
    label: str,
    result: dict,
    annotation: dict | None,
    readiness: str | None = None,
) -> DocumentScore:
    findings = result.get("findings") or []
    ann = annotation or {}
    return DocumentScore(
        doc_id=doc_id,
        label=ann.get("label", label),
        reviewed=bool(ann.get("reviewed")),
        readiness=readiness,
        must_fix=sum(1 for f in findings if f.get("priority") == "must_fix"),
        findings=len(findings),
        extraction=score_extraction(
            ann.get("expected"), result.get("extracted_financials")
        )
        if annotation
        else [],
        finding_score=score_findings(ann, findings) if annotation else None,
    )


def draft_annotation(document_sha256: str, label: str, result: dict) -> dict:
    """A starting point for a credit person to correct, never ground truth
    on its own: `expected` is what *this run* extracted, and must_find is
    left empty with the run's must-fix titles listed as candidates."""
    figures = {
        k: v
        for k, v in flatten_figures(result.get("extracted_financials")).items()
        if v is not None
    }
    expected: dict[str, Any] = {}
    years: dict[int, dict[str, float]] = defaultdict(dict)
    for key, value in figures.items():
        if key.startswith("years."):
            _, year, name = key.split(".", 2)
            years[int(year)][name] = value
        else:
            expected[key] = value
    if years:
        expected["years"] = {y: dict(v) for y, v in sorted(years.items())}
    return {
        "document_sha256": document_sha256,
        "label": label,
        "reviewed": False,
        "expected": expected,
        "must_find": [],
        "must_not_find": [],
        "candidate_must_find_titles": sorted(
            {
                f.get("title", "")
                for f in result.get("findings", [])
                if f.get("priority") == "must_fix"
            }
        ),
    }
