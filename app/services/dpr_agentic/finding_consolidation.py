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
    return min(
        group,
        key=lambda f: (_PROVENANCE_RANK.get(f.provenance, 99), len(f.explanation) * -1),
    )


def _merge_group(key: str, group: list[Finding]) -> Finding:
    base = _best_of(group).model_copy(deep=True)
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


def consolidate(findings: list[Finding]) -> list[Finding]:
    """Deduplicates `findings` by category + affected metric + source page
    (finding_rules.compute_dedup_key), merging evidence and picking one
    severity by rule (never "whichever duplicate came first"). Preserves
    input order of first occurrence for readability."""
    groups: dict[str, list[Finding]] = {}
    order: list[str] = []
    for f in findings:
        key = f.dedup_key or compute_dedup_key(f)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(f)
    return [_merge_group(key, groups[key]) for key in order]
