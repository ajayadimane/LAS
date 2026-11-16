"""Finding deduplication and consolidation (brief Section 5.1).

Root cause of the duplicate-findings bug: parallel_graph.py's dependency
graph feeds each agent its upstream agents' outputs as context (risk sees
finance/sector/research/policy; verifier sees risk; correction sees
verifier; report sees correction) so that it can correlate with them -- but
nothing ever merges the findings each of those agents independently restates
about the same underlying issue. `analyze()`/`analyze_parallel()` used to
just concatenate every agent's `findings` list. This module is the missing
step, run once, after the graph/sequential pipeline finishes and before an
AnalysisResult is built -- never inside an agent.

consolidate() is idempotent and order-independent: calling it twice, or on
an already-consolidated list, produces the same result.
"""

from __future__ import annotations

import re

from .finding_rules import (
    classify_finding_class,
    compute_dedup_key,
    pick_consolidated_severity,
    priority_for_narrative,
)
from .schemas import Finding

# Preference order when picking which duplicate's explanation/evidence
# becomes the merged finding's own text: a code-verified figure is more
# trustworthy prose than a narrative agent's restatement of the same issue.
_PROVENANCE_RANK = {
    "calculated": 0,
    "extracted": 1,
    "ai_interpreted": 2,
    "assumption": 3,
    "unverified": 4,
}


def _best_of(group: list[Finding]) -> Finding:
    # Code-calculated first, then a checklist answer (fixed title and
    # rating, checklists.py), then by provenance and fullest explanation.
    return min(
        group,
        key=lambda f: (
            0 if f.provenance == "calculated" else 1 if f.check_id else 2,
            _PROVENANCE_RANK.get(f.provenance, 99),
            len(f.explanation) * -1,
        ),
    )


def _merge_group(key: str, group: list[Finding]) -> Finding:
    base = _best_of(group).model_copy(deep=True)
    if not (base.check_id and base.provenance != "calculated"):
        # A checklist finding keeps its item's severity: a free-form
        # restatement must not re-rate it (that drift is what the
        # checklist removes).
        base.severity = pick_consolidated_severity(group)
    base.dedup_key = key
    # Sum, not len(group): a member may itself already be a merged result of
    # an earlier consolidate() call (idempotency -- re-running on an
    # already-consolidated list must not reset the count back to 1).
    base.merged_duplicate_count = sum(f.merged_duplicate_count for f in group)

    # Merge evidence: Finding carries a single (page, quote) pair, so any
    # distinct quotes from other duplicates are appended to the explanation
    # rather than dropped -- a real limitation of the current Finding shape
    # (see PROGRESS.md); the CAM snapshot's EvidenceReference list (milestone
    # 2's cam_adapter update) is where a full multi-quote model belongs.
    other_quotes = {
        f.evidence_quote
        for f in group
        if f.evidence_quote and f.evidence_quote != base.evidence_quote
    }
    if other_quotes:
        base.explanation += " Also reported with evidence: " + "; ".join(
            f'"{q}"' for q in sorted(other_quotes)
        )

    if base.finding_class is None:
        base.finding_class = classify_finding_class(base)
    if base.priority is None:
        base.priority, base.priority_rule = priority_for_narrative(base)
    return base


_TITLE_STOP = {
    "a",
    "an",
    "and",
    "the",
    "of",
    "in",
    "on",
    "for",
    "to",
    "or",
    "with",
    "from",
    "by",
    "is",
    "are",
    "not",
    "no",
}


def title_words(title: str) -> set[str]:
    """Title words without stop words, lightly stemmed, so rewordings such
    as "Lacking ... Projections" / "Lack ... Projection" compare equal."""
    out = set()
    for w in re.findall(r"[a-z0-9]+", title.casefold()):
        if w in _TITLE_STOP:
            continue
        for suffix in ("ing", "s"):
            if len(w) > len(suffix) + 3 and w.endswith(suffix):
                w = w[: -len(suffix)]
                break
        out.add(w)
    return out


def title_similarity(a: str, b: str) -> float:
    wa, wb = title_words(a), title_words(b)
    return len(wa & wb) / max(1, len(wa | wb))


def _page_compatible(a: Finding, b: Finding) -> bool:
    return (
        a.evidence_page is None
        or b.evidence_page is None
        or a.evidence_page == b.evidence_page
    )


# (a code finding's title, words an agent's restatement of it uses).
CALCULATED_TOPICS: tuple[tuple[re.Pattern, re.Pattern], ...] = tuple(
    (re.compile(title, re.I), re.compile(restated, re.I))
    for title, restated in (
        (r"^means of finance", r"means of finance|funding (?:total|table)"),
        (r"^cost of project", r"cost of project|project cost (?:total|table)"),
        (r"balance sheet does not balance", r"balance sheet\b.*\bbalanc"),
        (r"^stated dscr", r"\bdscr\b.*(?:formula|standard|stated|comput|method)"),
        (r"^fund flow", r"fund[\s-]*flow"),
        (r"^average dscr", r"average dscr"),
        (r"revenue does not match volume", r"revenue\b.*\b(?:volume|price|rate)"),
        (r"^full capacity assumed", r"(?:full|100\s*%) (?:capacity|utili[sz]ation)"),
        (r"^aif effective interest", r"subvention|effective (?:interest )?rate"),
    )
)


def consolidate(findings: list[Finding]) -> list[Finding]:
    """Deduplicates `findings` by category + affected metric + source page
    (finding_rules.compute_dedup_key), merging evidence and picking one
    severity by rule (never "whichever duplicate came first"). Preserves
    input order of first occurrence for readability."""
    from .checklists import TOPIC_TERMS

    groups: dict[str, list[Finding]] = {}
    order: list[str] = []
    # Checklist findings, collected first so the join below does not depend
    # on input order: a free-form finding on the same topic (by the item's
    # TOPIC_TERMS, whatever its category or page) restates the item. Seen
    # live in 11 of 15 reports: "No income tax provision made" next to the
    # checklist's "Income tax not provided for".
    checklist_keys = [
        (TOPIC_TERMS[f.check_id], f.dedup_key or compute_dedup_key(f))
        for f in findings
        if f.check_id in TOPIC_TERMS and f.provenance != "calculated"
    ]
    # Likewise for code findings: an agent restating one in other words
    # (seen live in 5 of 15 reports, e.g. "Means of Finance summation
    # error..." next to "Means Of Finance Total does not reconcile").
    calculated_keys = [
        (restated, f.dedup_key or compute_dedup_key(f))
        for f in findings
        if f.provenance == "calculated"
        for title, restated in CALCULATED_TOPICS
        if title.search(f.title)
    ]
    for f in findings:
        key = f.dedup_key or compute_dedup_key(f)
        if not f.check_id and f.provenance != "calculated":
            joined = next(
                (
                    k
                    for pattern, k in calculated_keys + checklist_keys
                    if pattern.search(f.title)
                ),
                None,
            )
            if joined is not None:
                if joined not in groups:
                    groups[joined] = []
                    order.append(joined)
                groups[joined].append(f)
                continue
        # For title-only non-calculation findings, merge near-synonymous
        # titles on the same page and category. Require substantial overlap,
        # so distinct issues are not silently collapsed.
        words = set(re.findall(r"[a-z0-9]+", f.title.casefold()))
        if f.provenance != "calculated":
            # An agent restating a code-calculated finding (often copying its
            # title verbatim from calculated_facts, under its own category)
            # joins that finding whatever its category or page: seen live as
            # "Means Of Finance Balances Cost does not reconcile" twice, once
            # financial_calculation and once project_cost. Near-identical
            # titles only, so a related-but-distinct issue stays separate.
            for prior_key in order:
                ref = groups[prior_key][0]
                if ref.provenance != "calculated":
                    continue
                other = set(re.findall(r"[a-z0-9]+", ref.title.casefold()))
                if (
                    len(words & other) >= 3
                    and len(words & other) / max(1, len(words | other)) >= 0.75
                ):
                    key = prior_key
                    break
        if f.provenance != "calculated" and key not in groups:
            # Seen live: the same narrative issue under two keys -- one
            # titled, one an issue topic -- or one copy without a page.
            # Same category, compatible page, and either the same issue
            # topic or the same title (after normalisation) is one finding.
            topic = key.rsplit(":", 1)[0] if key.startswith("issue:") else None
            for prior_key in order:
                ref = groups[prior_key][0]
                if ref.provenance == "calculated" or ref.category != f.category:
                    continue
                if not _page_compatible(ref, f):
                    continue
                same_topic = topic is not None and prior_key.rsplit(":", 1)[0] == topic
                if same_topic or title_words(ref.title) == title_words(f.title):
                    key = prior_key
                    break
        if (
            f.provenance != "calculated"
            and not key.startswith("issue:")
            and key not in groups
        ):
            for prior_key in order:
                group = groups[prior_key]
                ref = group[0]
                if (
                    ref.provenance == "calculated"
                    or ref.category != f.category
                    or ref.evidence_page != f.evidence_page
                ):
                    continue
                other = set(re.findall(r"[a-z0-9]+", ref.title.casefold()))
                if (
                    len(words & other) >= 3
                    and len(words & other) / max(1, len(words | other)) >= 0.57
                ):
                    key = prior_key
                    break
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(f)
    return [_merge_group(key, groups[key]) for key in order]
