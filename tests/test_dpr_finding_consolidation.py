"""Milestone 2: finding deduplication/consolidation (brief Section 5.1)."""

from app.services.dpr_agentic.finding_consolidation import consolidate
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


def test_consolidate_is_idempotent():
    findings = [_finding(), _finding()]
    once = consolidate(findings)
    twice = consolidate(once)
    assert len(once) == len(twice) == 1
    assert once[0].merged_duplicate_count == twice[0].merged_duplicate_count == 2
