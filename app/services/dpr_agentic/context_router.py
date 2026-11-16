"""Evidence-aware, bounded contexts for Gemini DPR agents.

This module *never* changes the original pages. Every excerpt retains its
physical page number. Section routing is conservative: ambiguous pages and
continuation tables are kept, rather than silently declaring a section absent.
"""
from __future__ import annotations

import re
from typing import Any

# Distinct topics are required for accurate coverage; keyword frequency alone
# is not a reliable indicator of a financial schedule.
SECTIONS = {
    "project_cost": ("cost of project", "project cost", "plant and machinery", "fixed capital", "total project outlay", "capital investment"),
    "means_of_finance": ("means of finance", "margin money", "promoter contribution", "source of finance", "bank finance", "term loan", "promoter share", "own contribution"),
    "repayment_schedule": ("repayment schedule", "loan repayment", "principal repayment", "interest repayment", "amortization", "repayment of loan", "repayment of term loan", "loan instalment", "installment", "principal due"),
    "dscr": ("dscr", "debt service coverage", "debt servicing"),
    "profit_and_loss": ("profit and loss", "profitability", "net profit", "profit before tax"),
    "cash_flow": ("cash flow", "cashflow", "cash & bank"),
    "balance_sheet": ("balance sheet", "assets and liabilities"),
    "working_capital": ("working capital", "receivables", "current assets"),
    "revenue_assumptions": ("sales assumptions", "gross receipts", "turnover", "revenue", "sales price"),
    "operating_costs": ("operating expenses", "fuel cost", "labour cost", "running cost", "maintenance"),
    "vendor_quotation": ("quotation", "pro-forma", "supplier", "vendor"),
    "operations": ("harvest", "operating days", "capacity utilization", "acre", "machinery", "technical",
                   "polyhouse", "poly house", "greenhouse", "shade net", "sq.mt", "sq. mt", "sq mt",
                   "milch", "milk yield", "lactation", "cold storage", "chamber"),
    "implementation": ("implementation schedule", "commissioning", "seasonality", "crop"),
    # The DPR's own summary ratios and key assumptions (the stated_ratios
    # extraction group only). "irr" alone would match "irrigation".
    "ratios": (
        "internal rate of return", "project irr", "(irr)", "irr:", "irr :",
        "irr %", "irr (", "break even", "break-even", "breakeven", "bep",
        "pay back", "payback", "pay-back", "debt equity", "debt-equity",
        "debt : equity", "debt:equity", "debt/equity", "fixed asset coverage",
        "facr", "ratio analysis", "financial indicators", "financial ratios",
        "key ratios",
    ),
    "assumptions": (
        "assumption", "escalation", "depreciation rate", "rate of depreciation",
        "written down value", "straight line method", "wdv", "s.l.m",
    ),
}
# unit_economics.py's checks: the sector agent sees these so it judges the
# inputs instead of re-deriving (and re-wording) the same arithmetic.
OPERATING_CHECKS = ("units_exceed_capacity", "year1_full_utilisation",
                    "revenue_rebuild", "negative_contribution",
                    "operating_days_idle")
# The ratio and assumption pages feed only the stated_ratios group; the
# agents' contexts are unchanged by them.
FINANCIAL = frozenset(SECTIONS) - {
    "operations", "implementation", "ratios", "assumptions"
}
SECTOR = {"operations", "implementation", "revenue_assumptions", "operating_costs", "vendor_quotation"}


def page_sections(text: str) -> set[str]:
    # LiteParse often breaks headings over physical lines. A heading like
    # "MEANS OF\nFINANCE" must still route to the financing extractor.
    lower = " ".join(text.casefold().split())
    return {section for section, terms in SECTIONS.items() if any(term in lower for term in terms)}


def document_index(pages: list) -> list[dict]:
    return [{"page": p.page, "sections": sorted(page_sections(p.text)),
             "length": len(p.text)} for p in pages]


def _section_relevance(page, section: str) -> tuple[int, int]:
    """Prefer actual schedule headings/tables over incidental keyword mentions.

    E.g., an early narrative saying 'repayment of term loan' should not
    displace an Annexure entitled 'Repayment Schedule' on a later page.
    """
    text = " ".join(page.text.casefold().split())
    terms = SECTIONS[section]
    title_lines = [line.strip() for line in text.splitlines()[:12] if line.strip()]
    title = " ".join(title_lines[:7])[:350]
    heading_score = sum(12 for term in terms if term in title)
    table_score = 3 if re.search(r"\b(?:year|yearly|particulars|principal|interest|amount|total)\b", text) else 0
    count = sum(min(text.count(term), 4) for term in terms)
    # Source richness is a tie-breaker, never a substitute for a heading.
    return (heading_score + table_score + count, len(text))


def select_evidence_pages(pages: list, sections: set[str], *, limit: int = 12) -> list:
    """Page-number-preserving, heading-aware evidence selection.

    Always allocate a page for each discoverable requested section first,
    then use remaining capacity for relevant annexures/continuations.
    Input pages are not mutated; omitted pages are reported by the context
    builder using partial_source and section_index.
    """
    ordered = sorted(pages, key=lambda p: p.page)
    by_page = {p.page: p for p in ordered}
    matches = {p.page: page_sections(p.text) & sections for p in ordered}
    candidates = [p for p in ordered if matches[p.page]]
    if not candidates:
        return ordered[:limit]
    selected: set[int] = set()
    # Rank source sections by what the page *is*, not simply what its prose
    # happens to mention; late-year financial annexures must be considered.
    for section in sorted(sections):
        options = [p for p in candidates if section in matches[p.page]]
        if options:
            best = max(options, key=lambda p: (_section_relevance(p, section), -p.page))
            selected.add(best.page)
    remaining = sorted(
        [p for p in candidates if p.page not in selected],
        key=lambda p: (max((_section_relevance(p, s)[0] for s in matches[p.page]), default=0), -p.page),
        reverse=True,
    )
    for p in remaining:
        if len(selected) >= limit:
            break
        selected.add(p.page)
        if re.search(r"\b(?:year\s*[1-9]|yr\s*[1-9]|continued|contd\.)\b", p.text, re.I):
            for neighbor in (p.page - 1, p.page + 1):
                if neighbor in by_page and len(selected) < limit:
                    selected.add(neighbor)
    # Pages chosen as section exemplars can themselves contain a table that
    # continues to the next page. Do not reserve neighbors only for pages
    # selected in the secondary relevance pass.
    for number in sorted(list(selected)):
        if len(selected) >= limit:
            break
        page = by_page[number]
        if re.search(r"\b(?:year\s*[1-9]|yr\s*[1-9]|continued|contd\.)\b", page.text, re.I):
            for neighbor in (number - 1, number + 1):
                if neighbor in by_page and len(selected) < limit:
                    selected.add(neighbor)
    return [by_page[num] for num in sorted(selected) if num in by_page][:limit]


def _compact_prior(prior: dict) -> dict:
    """Keep auditable findings and quoted evidence, drop redundant prose.
    Complete agent outputs remain in the persisted AnalysisResult.
    """
    result = {}
    for role, data in prior.items():
        if not isinstance(data, dict):
            continue
        result[role] = {
            "summary": str(data.get("summary", ""))[:300],
            "document_facts": data.get("document_facts", []) if role == "document" else [],
            "findings": [{key: f.get(key) for key in (
                "category", "severity", "title", "explanation", "evidence_page",
                "evidence_quote", "suggested_action", "verification", "provenance",
            ) if f.get(key) is not None} for f in data.get("findings", [])[:18]],
            "unresolved_questions": data.get("unresolved_questions", [])[:8],
        }
        result[role]["omitted_findings_count"] = max(0, len(data.get("findings", [])) - 18)
        for f in result[role]["findings"]:
            f["explanation"] = str(f.get("explanation", ""))[:220]
            f["suggested_action"] = str(f.get("suggested_action", ""))[:120]
            f["evidence_quote"] = str(f.get("evidence_quote") or "")[:210]
    return result


def financial_extraction_context(inp: Any, *, sections: set[str] | None = None, page_limit: int | None = None) -> dict:
    """Prioritize every discoverable financial schedule, not the full DPR.

    When the document has no recognizable schedule headings, the complete
    text is used up to the existing global length bound to avoid omissions.
    """
    chosen_sections = sections if sections is not None else set(FINANCIAL)
    selected = select_evidence_pages(inp.pages, chosen_sections, limit=page_limit or (10 if sections else 18))
    return {"project_context": inp.project_context,
            "pages": [{"page": p.page, "text": p.text} for p in selected],
            "section_index": document_index(inp.pages),
            "partial_source": len(selected) < len(inp.pages)}


def select_context(role: str, inp: Any, prior: dict, calculated_facts: list[dict]) -> dict:
    index = document_index(inp.pages)
    if role == "document":
        # The Document Agent needs identity/sector descriptions from across the
        # document, not the financial tables in full. Preserve all identity
        # mentions in a compact page-referenced index, including contradictions
        # in later appendices (e.g. another borrower name on a repayment page).
        ordered = sorted(inp.pages, key=lambda x: x.page)
        identity_lines = []
        identity_pattern = re.compile(
            r"\b(?:mr\.?|mrs\.?|shri|smt\.?|applicant|borrower|promoter|"
            r"proprietor|quotation|name of the|project location)\b", re.I
        )
        for p in ordered:
            matches = [line.strip()[:240] for line in p.text.splitlines()
                       if identity_pattern.search(line) and line.strip()]
            if matches:
                identity_lines.append({"page": p.page, "lines": matches[:4]})
        # Lines naming the documents the report's checklist asks about
        # (registration, returns, approvals, scheme portal, certificates),
        # wherever they appear.
        document_pattern = re.compile(
            r"udyam|udyog aadhaar|registration|regd\.?\s*no|\bitr\b|"
            r"income[\s-]*tax return|licen[cs]e|fssai|\bnoc\b|consent|"
            r"approval|certificate|training|\bedp\b|portal|application no",
            re.I,
        )
        document_lines = []
        for p in ordered:
            matches = [line.strip()[:200] for line in p.text.splitlines()
                       if document_pattern.search(line) and line.strip()]
            if matches:
                document_lines.append({"page": p.page, "lines": matches[:4]})
        intro = ordered[:3] + ordered[-2:]
        others = select_evidence_pages(ordered, {"vendor_quotation", "implementation"}, limit=4)
        selected = {p.page: p for p in intro + others}
        return {"dpr": {"project_context": inp.project_context,
                        "pages": [{"page": p.page, "text": p.text[:5000]}
                                  for p in sorted(selected.values(), key=lambda p: p.page)],
                        "identity_mentions": identity_lines,
                        "document_mentions": document_lines[:20],
                        "section_index": index,
                        "partial_source": len(selected) < len(ordered)},
                "previous_agent_outputs": {}, "calculated_facts": []}
    compact = _compact_prior(prior)
    if role == "planner":
        return {"dpr": {"project_context": inp.project_context, "section_index": index,
                        "document_summary": compact.get("document", {}).get("summary", ""),
                        "document_facts": compact.get("document", {}).get("document_facts", [])},
                "previous_agent_outputs": {}, "calculated_facts": []}
    if role in ("risk", "verifier", "correction", "report"):
        evidence_pages = {f.get("evidence_page") for output in compact.values()
                          for f in output.get("findings", []) if isinstance(f.get("evidence_page"), int)}
        # Verification is grounded in original page quotations, not prior agents alone.
        excerpts = []
        if role == "verifier":
            # Supply excerpts around the *claimed* quote. Earlier code sent
            # page[:4500], which missed claims near the bottom of long pages.
            quoted_by_page = {}
            for output in compact.values():
                for finding in output.get("findings", []):
                    page = finding.get("evidence_page")
                    quote = finding.get("evidence_quote")
                    if isinstance(page, int) and quote:
                        quoted_by_page.setdefault(page, []).append(quote)
            for p in inp.pages:
                if p.page not in evidence_pages:
                    continue
                snippets = []
                for quote in quoted_by_page.get(p.page, [])[:4]:
                    offset = p.text.casefold().find(quote.casefold())
                    if offset >= 0:
                        snippets.append(p.text[max(0, offset-280):offset+len(quote)+320])
                if not snippets:
                    snippets.append(p.text[:1200])
                excerpts.append({"page": p.page, "text": "\n[...]\n".join(snippets)[:2400]})
            excerpts = excerpts[:10]
        elif role == "risk":
            # The risk checklist (one asset without cover against its loss, demand
            # concentration, promoter experience, sensitivity) must be
            # judged from the DPR itself. Seen live: with only other
            # agents' summaries, the risk agent answered "cannot_tell" --
            # "no DPR source pages were provided".
            excerpts = [
                {"page": p.page, "text": p.text[:3500]}
                for p in select_evidence_pages(inp.pages, set(SECTOR), limit=6)
            ]
        return {"dpr": {"project_context": inp.project_context,
                        "section_index": index if role == "verifier" else [],
                        "source_pages": excerpts},
                "previous_agent_outputs": compact,
                "calculated_facts": calculated_facts if role in ("risk", "verifier") else []}
    sections = FINANCIAL if role == "finance" else SECTOR if role == "sector" else {"vendor_quotation"}
    chosen = select_evidence_pages(inp.pages, set(sections), limit=10 if role == "finance" else 8)
    return {"dpr": {"project_context": inp.project_context,
                    "pages": [{"page": p.page, "text": p.text} for p in chosen],
                    "section_index": index, "partial_source": len(chosen) < len(inp.pages)},
            "previous_agent_outputs": compact,
            "calculated_facts": calculated_facts if role == "finance"
            else [f for f in calculated_facts
                  if str(f.get("check", "")).startswith(OPERATING_CHECKS)]
            if role == "sector" else []}


def verifier_judge_context(
    inp: Any, candidates: list[dict], calculated_facts: list[dict]
) -> dict:
    """The verifier judges the consolidated candidate list by finding_id,
    against source excerpts around each claimed quote (the same windowing
    as select_context's verifier branch) and the code-verified facts."""
    compact = [
        {
            key: (str(c.get(key))[:limit] if limit else c.get(key))
            for key, limit in (
                ("finding_id", 0), ("category", 0), ("severity", 0),
                ("priority", 0), ("provenance", 0), ("title", 200),
                ("explanation", 320), ("evidence_page", 0),
                ("evidence_quote", 240), ("verification", 0),
            )
            if c.get(key) is not None
        }
        for c in candidates
    ]
    quoted_by_page: dict[int, list[str]] = {}
    for c in candidates:
        page, quote = c.get("evidence_page"), c.get("evidence_quote")
        if isinstance(page, int) and quote:
            quoted_by_page.setdefault(page, []).append(quote)
    excerpts = []
    for p in sorted(inp.pages, key=lambda x: x.page):
        quotes = quoted_by_page.get(p.page)
        if not quotes:
            continue
        snippets = []
        for quote in quotes[:4]:
            offset = p.text.casefold().find(quote.casefold())
            if offset >= 0:
                snippets.append(p.text[max(0, offset - 280):offset + len(quote) + 320])
        if not snippets:
            snippets.append(p.text[:1200])
        excerpts.append({"page": p.page, "text": "\n[...]\n".join(snippets)[:2400]})
    return {
        "dpr": {
            "project_context": inp.project_context,
            "section_index": document_index(inp.pages),
            "source_pages": excerpts[:12],
        },
        "candidate_findings": compact,
        "calculated_facts": calculated_facts,
    }
