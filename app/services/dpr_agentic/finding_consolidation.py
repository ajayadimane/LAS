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
    _SEVERITY_RANK,
    classify_finding_class,
    compute_dedup_key,
    pick_consolidated_severity,
    priority_for_narrative,
    raise_priority_from,
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
    if base.provenance == "calculated":
        # A calculated finding's severity is set by code from the figures; a
        # free-form restatement must not re-rate it (same rule as
        # finding_judge._absorb). Seen live (eval 2026-11-25): three agents
        # called a 50% means-of-finance gap "critical", the merge took that
        # over the code's "high", and the report's readiness flipped to
        # EXPERT_REVIEW_REQUIRED on the wording of one run.
        base.severity = pick_consolidated_severity(
            [f for f in group if f.provenance == "calculated"]
        )
    elif not base.check_id:
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
    raise_priority_from(base, group)
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


# Words too common in finding titles to show that two findings are about the
# same thing ("Fuel cost per year not budgeted" / "Repair cost per year not
# budgeted" share only these), and synonyms the agents swap freely.
_GENERIC_TITLE_WORDS = frozenset(
    "cost year budgeted budget table miss missing amount total figure show "
    "shown include included expense plan project dpr annual value no not per "
    # Judgement words: they say how bad, not what about ("Fuel cost
    # understated versus ..." / "Crew wages understated versus ...").
    "understated overstated versu vs mismatch discrepancy inconsistent "
    "inconsistency omitted omission unrealistic".split()
)
_TITLE_SYNONYMS = {"freight": "transport", "transit": "transport"}


def _canonical_words(title: str) -> set[str]:
    return {_TITLE_SYNONYMS.get(w, w) for w in title_words(title)}


_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _numbers_differ(a: str, b: str) -> bool:
    """Both titles carry numbers and neither title's numbers include the
    other's: "DSCR Year 1 verified" / "DSCR Year 2 verified" are two
    findings, though every other word matches (seen merged in real runs). A
    number in only one title, or one title naming more of the same years
    ("... outdated FY 2021-22 and FY 2022-23" / "... outdated FY 2022-23"),
    is no bar."""
    na, nb = set(_NUMBER.findall(a)), set(_NUMBER.findall(b))
    return bool(na and nb and not (na <= nb or nb <= na))


def _specific_shared(a: str, b: str) -> set[str]:
    return (_canonical_words(a) & _canonical_words(b)) - _GENERIC_TITLE_WORDS


# An explanation shorter than this says too little for its wording to show
# that two findings are the same.
MIN_EXPLANATION_WORDS = 8
EXPLANATION_SIMILARITY = 0.5


def same_issue_by_explanation(a: Finding, b: Finding) -> bool:
    """Two free-form findings on the same page and category that explain
    the same thing in near-identical words, under titles sharing at least
    two specific words. Seen live (91364956ce58, 2026-11-25): "intermediate
    projection years missing from balance sheet and cost tables" / "expense
    and balance sheet tables skip alternate years", both page 20, both
    explained as tables showing only 2026, 2028, 2030 and 2032. Measured on
    every saved eval run: at this similarity nearly every pair is a true
    duplicate; the exception ("Exact alignment of project cost ..." / "Verification of
    promoter margin ...") shares no title word."""
    if a.evidence_page is None or a.evidence_page != b.evidence_page:
        return False
    if a.category != b.category or _numbers_differ(a.title, b.title):
        return False
    wa, wb = title_words(a.explanation), title_words(b.explanation)
    if min(len(wa), len(wb)) < MIN_EXPLANATION_WORDS:
        return False
    if len(wa & wb) / max(1, len(wa | wb)) < EXPLANATION_SIMILARITY:
        return False
    return len(_specific_shared(a.title, b.title)) >= 2


def same_issue_on_same_page(a: Finding, b: Finding) -> bool:
    """Two free-form findings on the same page and category whose titles,
    with synonyms folded, share at least three words, two of them specific,
    and half of all their words. Seen live (91364956ce58, 2026-11-25):
    "freight and transit costs from Bhopal are not budgeted" and "transport
    cost from Bhopal factory is not budgeted", both page 27, printed twice.
    Distinct issues that share their topic words ("Running cost per acre
    understated ..." / "Break-even acres understated versus corrected
    running cost") stay apart."""
    if a.evidence_page is None or a.evidence_page != b.evidence_page:
        return False
    if a.category != b.category or _numbers_differ(a.title, b.title):
        return False
    wa, wb = _canonical_words(a.title), _canonical_words(b.title)
    shared = wa & wb
    return (
        len(shared) >= 3
        and len(shared - _GENERIC_TITLE_WORDS) >= 2
        and len(shared) / max(1, len(wa | wb)) >= 0.5
    )


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
        (
            r"^means of finance",
            r"means of finance|funding (?:total|table)|total funding|"
            r"project cost (?:does not|doesn't) match",
        ),
        (
            r"^cost of project",
            r"cost of project|project cost (?:total|table)|total funding|"
            r"project cost (?:does not|doesn't) match",
        ),
        (r"balance sheet does not balance", r"balance sheet\b.*\bbalanc"),
        (
            r"^stated dscr",
            r"(?:\bdscr\b|debt (?:service )?coverage|repayment coverage|"
            r"loan coverage|coverage ratio)\b.*"
            r"(?:formula|standard|stated|comput|method|calculat|incorrect|"
            r"wrong|error)",
        ),
        (r"^fund flow", r"fund[\s-]*flow"),
        (r"^average dscr", r"average dscr"),
        (r"revenue does not match volume", r"revenue\b.*\b(?:volume|price|rate)"),
        (
            r"^full capacity assumed",
            r"(?:full|100\s*%)(?: \w+)? (?:capacity|utili[sz]ation)|"
            r"(?:first|year[\s-]*1|initial)[\s-]*year\b.*\b(?:set[\s-]*up|"
            r"implementation|start[\s-]*up|ramp)",
        ),
        (r"^aif effective interest", r"subvention|effective (?:interest )?rate"),
        # "Loan repayment fails if service rates fall twenty percent" (eval
        # 2026-11-25) next to the code's "Repayment fails if the price falls
        # 20%".
        (r"^repayment fails if", r"repayment\b.*\bfails?\b|fails? if .*\bfall"),
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
    from .checklists import TOPIC_PRECEDENCE

    # Most specific item first when a title matches several (checklists.py).
    checklist_keys = [
        (
            TOPIC_TERMS[f.check_id],
            f.dedup_key or compute_dedup_key(f),
            f.severity,
            f.evidence_page,
        )
        for f in sorted(
            (
                f
                for f in findings
                if f.check_id in TOPIC_TERMS and f.provenance != "calculated"
            ),
            key=lambda f: TOPIC_PRECEDENCE.get(f.check_id, len(TOPIC_PRECEDENCE)),
        )
    ]
    # Likewise for code findings: an agent restating one in other words
    # (seen live in 5 of 15 reports, e.g. "Means of Finance summation
    # error..." next to "Means Of Finance Total does not reconcile").
    calculated_keys = [
        (restated, f.dedup_key or compute_dedup_key(f), f.severity, f.evidence_page)
        for f in findings
        if f.provenance == "calculated"
        for title, restated in CALCULATED_TOPICS
        if title.search(f.title)
    ]
    for f in findings:
        key = f.dedup_key or compute_dedup_key(f)
        if not f.check_id and f.provenance != "calculated":
            # Never into a less severe anchor: a topic word is a loose
            # match, and the anchor keeps its own text and rating, so a more
            # severe finding joining it would vanish (seen: a critical
            # "Promoter contribution of 2% is below the 10% margin" folded
            # into "Promoter's experience not shown", medium). Unless both
            # cite the same page: topic and page together are the same issue
            # (seen live: "Revenue does not show billing rates or acreage",
            # high, beside "Your income is not worked out step by step",
            # medium, both page 8), and raise_priority_from in _merge_group
            # still keeps the higher priority the customer sees. One step
            # only: a critical finding never joins a medium one.
            rank = _SEVERITY_RANK.get(f.severity, 99)
            joined = next(
                (
                    k
                    for pattern, k, anchor_severity, anchor_page in (
                        calculated_keys + checklist_keys
                    )
                    if pattern.search(f.title)
                    and (
                        rank >= _SEVERITY_RANK.get(anchor_severity, 99)
                        or (
                            anchor_page is not None
                            and f.evidence_page == anchor_page
                            and _SEVERITY_RANK.get(anchor_severity, 99) - rank == 1
                        )
                    )
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
                if (
                    same_topic
                    or title_words(ref.title) == title_words(f.title)
                    or same_issue_on_same_page(ref, f)
                    or same_issue_by_explanation(ref, f)
                ):
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
                    # Filler words alone ("Fuel / Repair cost per year not
                    # budgeted") and a different year or figure ("DSCR Year
                    # 1 / 2 verified") are not the same issue. Measured on
                    # every saved eval run: no true merge needs fewer than
                    # two specific shared words.
                    and len(_specific_shared(ref.title, f.title)) >= 2
                    and not _numbers_differ(ref.title, f.title)
                ):
                    key = prior_key
                    break
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(f)
    return [_merge_group(key, groups[key]) for key in order]
