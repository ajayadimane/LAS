"""Milestone 2: the deterministic priority/classification rules (Section 7)."""

from app.services.dpr_agentic.finding_rules import (
    DEFAULT_THRESHOLDS,
    classify_finding_class,
    priority_for_calculation,
    priority_for_narrative,
)
from app.services.dpr_agentic.schemas import Finding


def test_calculated_mismatch_above_threshold_is_must_fix():
    priority, rule = priority_for_calculation(
        check="cost_of_project_total", computed_value=107.0, stated_value=100.0
    )
    assert priority == "must_fix"
    assert "7.0%" in rule


def test_calculated_mismatch_below_threshold_is_should_fix():
    priority, rule = priority_for_calculation(
        check="cost_of_project_total", computed_value=102.0, stated_value=100.0
    )
    assert priority == "should_fix"
    assert "2.0%" in rule


def test_mismatch_exactly_at_threshold_is_must_fix():
    priority, _ = priority_for_calculation(
        check="means_of_finance_total", computed_value=105.0, stated_value=100.0
    )
    assert priority == "must_fix"


def test_year_one_dscr_below_one_is_must_fix():
    priority, rule = priority_for_calculation(
        check="dscr_year_1", computed_value=0.8, stated_value=None
    )
    assert priority == "must_fix"
    assert "year-1" in rule.lower()


def test_later_year_dscr_below_one_is_should_fix_not_must_fix():
    priority, _ = priority_for_calculation(
        check="dscr_year_2", computed_value=0.9, stated_value=None
    )
    assert priority == "should_fix"


def test_healthy_dscr_is_good_to_fix():
    priority, _ = priority_for_calculation(
        check="dscr_year_1", computed_value=1.8, stated_value=None
    )
    assert priority == "good_to_fix"


def test_missing_stated_total_falls_back_to_should_fix():
    priority, _ = priority_for_calculation(
        check="means_of_finance_balances_cost", computed_value=10.0, stated_value=None
    )
    assert priority == "should_fix"


def test_custom_threshold_is_honored():
    from dataclasses import replace

    loose = replace(DEFAULT_THRESHOLDS, must_fix_impact_pct=10.0)
    priority, _ = priority_for_calculation(
        check="cost_of_project_total",
        computed_value=107.0,
        stated_value=100.0,
        thresholds=loose,
    )
    assert priority == "should_fix"


def test_missing_document_is_always_must_fix():
    finding = Finding(
        category="missing_document",
        severity="low",
        title="Quotation missing",
        explanation="x",
        suggested_action="Request it.",
    )
    priority, _ = priority_for_narrative(finding)
    assert priority == "must_fix"


def test_narrative_high_severity_is_should_fix():
    finding = Finding(
        category="assumption",
        severity="high",
        title="x",
        explanation="x",
        suggested_action="x",
    )
    assert priority_for_narrative(finding)[0] == "should_fix"


def test_narrative_medium_severity_is_good_to_fix():
    finding = Finding(
        category="assumption",
        severity="medium",
        title="x",
        explanation="x",
        suggested_action="x",
    )
    assert priority_for_narrative(finding)[0] == "good_to_fix"


def test_known_document_defect_category_classified_correctly():
    finding = Finding(
        category="financial_calculation",
        severity="high",
        title="x",
        explanation="x",
        suggested_action="x",
    )
    assert classify_finding_class(finding) == "document_defect"


def test_unknown_category_classified_as_business_risk():
    finding = Finding(
        category="single_asset_dependency",
        severity="medium",
        title="x",
        explanation="x",
        suggested_action="x",
    )
    assert classify_finding_class(finding) == "business_risk"
