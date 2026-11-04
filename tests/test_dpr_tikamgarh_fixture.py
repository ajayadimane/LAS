"""Milestone 2b: the Tikamgarh golden-case CAM snapshot fixture.

tests/fixtures/tikamgarh_cam_snapshot.json is produced by
scripts/build_tikamgarh_fixture.py -- a full orchestrator.analyze() run
(not a hand-built snapshot) against a FakeLLM returning the golden figures,
with cam_engine_enabled=True. See that script's docstring and PROGRESS.md
for why this is a simulation (no real Tikamgarh DPR file exists) rather
than a real document run, and why that's still a meaningful regression
case: every figure is independently derived and cross-checked, not copied
from the sample report's headline numbers.

This is the primary fixture milestone 4's templates/PDF work renders
against.
"""

import json
from decimal import Decimal
from pathlib import Path

from app.services.dpr_agentic.cam_snapshot import CAMSnapshot
from app.services.dpr_agentic.cam_verification import verify_snapshot

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "tikamgarh_cam_snapshot.json"


def _load() -> CAMSnapshot:
    return CAMSnapshot.model_validate(json.loads(FIXTURE_PATH.read_text()))


def test_fixture_exists_and_parses():
    snapshot = _load()
    assert snapshot.report_id == "RPT-TIKAMGARH-GOLDEN"


def test_fixture_passes_all_13_verification_checks_with_zero_issues():
    snapshot = _load()
    result = verify_snapshot(snapshot)
    assert result.is_publishable
    assert result.issues == []


def test_fixture_dscr_matches_the_golden_case():
    snapshot = _load()
    assert snapshot.financial_ratios.average_dscr.computed_value == Decimal("1.97")


def test_fixture_breakeven_matches_the_golden_case():
    snapshot = _load()
    assert snapshot.financial_ratios.breakeven_accounting.computed_value == Decimal(
        "726"
    )


def test_fixture_repayment_schedule_matches_the_golden_case():
    snapshot = _load()
    year1 = snapshot.repayment_schedule[0]
    assert year1.principal_due.amount == Decimal("3.21")
    assert year1.interest_due.amount == Decimal("2.03")
    assert year1.total_debt_service.amount == Decimal("5.24")


def test_fixture_scenarios_are_present_and_labeled():
    snapshot = _load()
    by_id = {s.scenario_id: s for s in snapshot.scenario_analysis}
    assert by_id["dpr_as_written"].is_hypothetical is False
    assert by_id["dpr_as_written"].dscr_by_year[1].computed_value == Decimal("1.97")
    assert by_id["corrected_costs"].is_hypothetical is True
    assert by_id["corrected_costs"].dscr_by_year[1].computed_value == Decimal("1.49")


def test_fixture_findings_are_deduplicated():
    snapshot = _load()
    keys = [f.dedup_key for f in snapshot.findings]
    assert len(keys) == len(set(keys))
    assert len(snapshot.findings) <= 13
    capacity_finding = next(
        f for f in snapshot.findings if "acreage" in f.title.lower()
    )
    # 5 narrative duplicates (the fixture's own fake multi-agent chain) plus
    # the calculated acres-per-year-feasibility finding
    # (cam_engine_bridge.py), merged into one via the shared "assumption"
    # category + normalized title.
    assert capacity_finding.merged_duplicate_count == 6


def test_fixture_contains_no_unresolved_placeholders():
    """The sample strings this fixture's own derivation was based on must
    not leak in as literal unresolved template text."""
    raw = FIXTURE_PATH.read_text()
    assert "{{" not in raw
    assert "[Customer full name]" not in raw
    assert "[BD name]" not in raw


def test_fixture_borrower_name_is_not_invented():
    snapshot = _load()
    assert snapshot.borrower_profile.borrower_name is None
