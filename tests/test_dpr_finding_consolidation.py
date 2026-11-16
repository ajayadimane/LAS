"""Milestone 2: finding deduplication/consolidation (brief Section 5.1)."""

from app.services.dpr_agentic.finding_consolidation import consolidate
from app.services.dpr_agentic.finding_rules import compute_dedup_key
from app.services.dpr_agentic.schemas import Finding


def _finding(**overrides) -> Finding:
    defaults = dict(
        category="financial_calculation",
        severity="medium",
        title="Cost of project does not reconcile",
        explanation="x",
        evidence_page=3,
        suggested_action="Verify.",
        provenance="ai_interpreted",
    )
    defaults.update(overrides)
    return Finding(**defaults)


def test_duplicates_from_different_agents_merge_into_one():
    findings = [
        _finding(explanation="risk agent restates this"),
        _finding(explanation="verifier restates this"),
        _finding(explanation="correction restates this"),
    ]
    merged = consolidate(findings)
    assert len(merged) == 1
    assert merged[0].merged_duplicate_count == 3


def test_no_two_consolidated_findings_share_a_dedup_key():
    findings = [
        _finding(title="Issue A"),
        _finding(title="Issue A"),
        _finding(title="Issue B", evidence_page=5),
        _finding(category="dscr", title="Issue C"),
    ]
    merged = consolidate(findings)
    keys = [f.dedup_key for f in merged]
    assert len(keys) == len(set(keys))


def test_severity_on_merge_is_the_most_severe_not_the_first():
    findings = [
        _finding(severity="low"),
        _finding(severity="critical"),
        _finding(severity="medium"),
    ]
    merged = consolidate(findings)
    assert len(merged) == 1
    assert merged[0].severity == "critical"


def test_distinct_evidence_quotes_are_preserved_not_dropped():
    findings = [
        _finding(evidence_quote="quote one"),
        _finding(evidence_quote="quote two"),
    ]
    merged = consolidate(findings)
    assert len(merged) == 1
    assert (
        "quote two" in merged[0].explanation or merged[0].evidence_quote == "quote two"
    )


def test_distinct_titles_or_pages_are_not_merged():
    findings = [
        _finding(title="Issue A", evidence_page=1),
        _finding(title="Issue B", evidence_page=1),
        _finding(title="Issue A", evidence_page=2),
    ]
    merged = consolidate(findings)
    assert len(merged) == 3


def test_calculated_finding_keeps_its_own_priority_from_financial_calc():
    """A calculated finding already carries priority/priority_rule from
    financial_calc._finalize(); consolidation must not overwrite it."""
    calculated = _finding(
        provenance="calculated",
        priority="must_fix",
        priority_rule="MUST_FIX: test",
        finding_class="document_defect",
    )
    merged = consolidate([calculated])
    assert merged[0].priority == "must_fix"
    assert merged[0].priority_rule == "MUST_FIX: test"


def test_narrative_finding_gets_a_priority_assigned_on_consolidation():
    findings = [_finding(severity="high", priority=None)]
    merged = consolidate(findings)
    assert merged[0].priority == "should_fix"
    assert merged[0].priority_rule


def test_business_risk_category_is_not_classified_as_document_defect():
    findings = [_finding(category="seasonal_demand_risk")]
    merged = consolidate(findings)
    assert merged[0].finding_class == "business_risk"


def test_calculated_finding_and_its_narrative_restatement_merge():
    """Same title, category and page: the narrative copy's explanation
    mentions harvester operating days, which used to give it a topic key
    the calculated copy did not share, so the report showed both."""
    calculated = Finding(
        category="assumption",
        severity="high",
        title="Claimed annual acreage exceeds plausible machine throughput",
        explanation="Acres/day x days/season x seasons/year = 600 acres/year; "
        "the document plans for 1200.",
        evidence_page=4,
        suggested_action="Verify this figure with the applicant.",
        provenance="calculated",
        verification="source_verified",
    )
    narrative = Finding(
        category="assumption",
        severity="high",
        title="Claimed annual acreage exceeds plausible machine throughput",
        explanation="At 10 acres/day over 30 operating days across 2 seasons, "
        "one harvester can cover 300-600 acres/year.",
        evidence_page=4,
        suggested_action="Revise the acreage.",
    )
    [merged] = consolidate([calculated, narrative])
    assert merged.provenance == "calculated"
    assert merged.merged_duplicate_count == 2


def test_explanation_wording_alone_does_not_pull_in_a_topic():
    fuel = Finding(
        category="financial_calculation",
        severity="high",
        title="Fuel cost understated",
        explanation="Fuel for the harvester's operating days is priced too low.",
        evidence_page=8,
        suggested_action="Reprice fuel.",
    )
    assert not compute_dedup_key(fuel).startswith("issue:")


def test_consolidate_is_idempotent():
    findings = [_finding(), _finding()]
    once = consolidate(findings)
    twice = consolidate(once)
    assert len(once) == len(twice) == 1
    assert once[0].merged_duplicate_count == twice[0].merged_duplicate_count == 2


def test_agent_restating_a_calculated_finding_merges_across_categories():
    """Seen live: an agent copied a calculated title under its own category."""
    calculated = Finding(
        category="financial_calculation",
        severity="high",
        title="Means Of Finance Balances Cost does not reconcile",
        explanation="Computed 29.40 vs stated 29.30.",
        suggested_action="Verify.",
        provenance="calculated",
        verification="source_verified",
    )
    restated = Finding(
        category="project_cost",
        severity="high",
        title="Means of Finance Balances Cost does not reconcile",
        explanation="Funding exceeds cost by 0.10 lakh.",
        evidence_page=4,
        suggested_action="Align totals.",
    )
    [merged] = consolidate([calculated, restated])
    assert merged.provenance == "calculated" and merged.merged_duplicate_count == 2


def test_a_related_but_distinct_title_stays_separate_from_a_calculation():
    calculated = Finding(
        category="financial_calculation",
        severity="high",
        title="Cost Of Project Total does not reconcile",
        explanation="x",
        suggested_action="Verify.",
        provenance="calculated",
    )
    other = Finding(
        category="project_cost",
        severity="medium",
        title="Project cost excludes working capital margin",
        explanation="y",
        evidence_page=4,
        suggested_action="Add margin.",
    )
    assert len(consolidate([calculated, other])) == 2


def test_same_title_under_a_topic_key_and_a_title_key_merges():
    """Seen live: one copy keyed by its title words, the verifier's copy by
    an issue topic picked up from its explanation."""
    a = _finding(
        category="operations",
        title="Aggressive Capacity Utilization during Narrow Harvest Windows",
        evidence_page=7,
        explanation="x",
    )
    b = a.model_copy(
        update={"dedup_key": "issue:day_one_full_utilization:7", "explanation": "y"}
    )
    assert len(consolidate([a, b])) == 1


def test_same_issue_topic_with_and_without_a_page_merges():
    a = _finding(
        category="missing_document",
        title="Vendor Quotation Not Provided",
        evidence_page=None,
        dedup_key="issue:missing_vendor_quote:unknown",
    )
    b = _finding(
        category="missing_document",
        title="Vendor Quotation Omitted",
        evidence_page=7,
        dedup_key="issue:missing_vendor_quote:7",
    )
    [merged] = consolidate([a, b])
    assert merged.merged_duplicate_count == 2


def test_same_title_on_different_pages_stays_separate():
    a = _finding(title="Interest omitted from P&L", evidence_page=7)
    b = _finding(title="Interest omitted from P&L", evidence_page=9)
    assert len(consolidate([a, b])) == 2
