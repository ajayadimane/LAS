"""Applies the verifier's verdicts to the consolidated candidate findings.

The verifier (a Gemini role) judges every candidate by id: keep it, reject
it, merge it into a duplicate, downgrade its severity, or mark it as needing
the borrower's clarification. Gemini decides; this module enforces what a
verdict is allowed to do:

- A code-calculated finding (provenance "calculated") is never rejected or
  downgraded, and its priority never changes -- an LLM cannot overrule
  arithmetic. If the verifier says it duplicates a narrative finding, the
  narrative one is folded into it instead.
- A verdict naming an unknown id, merging into itself or into a finding
  that is itself being removed, or "downgrading" to an equal or higher
  severity is ignored.
- A finding with no verdict is kept: silence is not rejection.
- Rejected findings are returned separately, with the reason, for the
  audit trail -- never deleted.
- Severity changes recompute the narrative priority, by the same rule as
  everywhere else (finding_rules.priority_for_narrative).

Pure functions; no LLM, no I/O.
"""

from __future__ import annotations

from .finding_rules import priority_for_narrative
from .schemas import Finding, Verdict

_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def assign_ids(findings: list[Finding]) -> list[Finding]:
    """Stable ids F01, F02, ... in list order (copies; inputs untouched)."""
    out = []
    for i, f in enumerate(findings, start=1):
        g = f.model_copy(deep=True)
        g.finding_id = f"F{i:02d}"
        out.append(g)
    return out


def _absorb(target: Finding, duplicate: Finding) -> None:
    target.merged_duplicate_count += duplicate.merged_duplicate_count
    if _SEVERITY_RANK.get(duplicate.severity, 9) < _SEVERITY_RANK.get(
        target.severity, 9
    ):
        if target.provenance != "calculated" and not target.check_id:
            target.severity = duplicate.severity
            target.priority, target.priority_rule = priority_for_narrative(target)
    if duplicate.evidence_quote and duplicate.evidence_quote != target.evidence_quote:
        target.explanation += (
            f' Also reported with evidence: "{duplicate.evidence_quote}"'
        )


def _anchor_rank(f: Finding) -> int:
    """0 code-calculated, 1 checklist item (fixed title and rating,
    checklists.py), 2 free-form. On a merge the lower rank absorbs the
    other. Seen live: a must-fix checklist quotation finding merged into a
    free-form one rated should-fix, and the must-fix disappeared."""
    if f.provenance == "calculated":
        return 0
    return 1 if f.check_id else 2


def apply_verdicts(
    candidates: list[Finding], verdicts: list[Verdict]
) -> tuple[list[Finding], list[Finding]]:
    """Returns (kept, rejected), in the candidates' order."""
    by_id = {f.finding_id: f.model_copy(deep=True) for f in candidates if f.finding_id}
    order = [f.finding_id for f in candidates if f.finding_id in by_id]
    removed: set[str] = set()
    rejected: list[Finding] = []

    for v in verdicts:
        f = by_id.get(v.finding_id)
        if f is None or v.finding_id in removed:
            continue
        calculated = f.provenance == "calculated"
        if v.decision == "reject":
            if calculated:
                continue
            f.rejection_reason = v.reason or "Rejected by the verifier."
            rejected.append(f)
            removed.add(v.finding_id)
        elif v.decision == "merge":
            target_id = v.duplicate_of
            target = by_id.get(target_id) if target_id else None
            if target is None or target_id == v.finding_id or target_id in removed:
                continue
            mine, theirs = _anchor_rank(f), _anchor_rank(target)
            if mine == theirs < 2:
                continue  # two calculations / two checklist items never merge
            if mine < theirs:
                # Keep the code-verified / checklist finding; fold the
                # other one in.
                _absorb(f, target)
                removed.add(target_id)
            else:
                _absorb(target, f)
                removed.add(v.finding_id)
        elif v.decision == "downgrade":
            # A checklist finding's severity is fixed by its item.
            if calculated or f.check_id or v.new_severity is None:
                continue
            if _SEVERITY_RANK[v.new_severity] <= _SEVERITY_RANK.get(f.severity, 9):
                continue
            f.severity = v.new_severity
            f.priority, f.priority_rule = priority_for_narrative(f)
        elif v.decision == "needs_clarification":
            f.verification = "needs_clarification"
        # "keep": nothing to do.

    kept = [by_id[i] for i in order if i not in removed]
    return kept, rejected


# A verifier's "new" finding needs only this much title overlap with a kept
# finding (same category, compatible page) to count as a restatement: it was
# asked for issues no candidate covers, so the bar for "new" is high.
RESTATEMENT_SIMILARITY = 0.5


def absorb_restatements(kept: list[Finding], new: list[Finding]) -> list[Finding]:
    """Folds each of the verifier's new findings that restates a kept one
    into it (seen live: kept "Total Absence of Working Capital Allocation..."
    plus new "Zero Working Capital Provisioning..."). Returns kept + the
    genuinely new findings; inputs are not modified."""
    from .checklists import TOPIC_TERMS
    from .finding_consolidation import title_similarity

    out = [f.model_copy(deep=True) for f in kept]
    for n in new:
        # A checklist finding of the same topic, whatever the category: the
        # item's fixed title and priority win.
        target = next(
            (
                k
                for k in out
                if k.check_id in TOPIC_TERMS
                and not n.check_id
                and TOPIC_TERMS[k.check_id].search(n.title)
            ),
            None,
        )
        if target is not None:
            _absorb(target, n)
            continue
        target = next(
            (
                k
                for k in out
                if k.category == n.category
                and (
                    k.evidence_page is None
                    or n.evidence_page is None
                    or k.evidence_page == n.evidence_page
                )
                and title_similarity(k.title, n.title) >= RESTATEMENT_SIMILARITY
            ),
            None,
        )
        if target is None:
            out.append(n.model_copy(deep=True))
        else:
            _absorb(target, n)
    return out
