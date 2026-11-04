"""Milestone 1: CAMSnapshot model validity and the AnalysisResult adapter.

Does not test financial computation, dedup, severity rules or the
verification layer -- those land in milestones 2-3 (see PROGRESS.md).
"""

import hashlib

from app.services.dpr_agentic.cam_adapter import build_snapshot_from_analysis
from app.services.dpr_agentic.cam_snapshot import (
    CAM_SNAPSHOT_SCHEMA_VERSION,
    BorrowerProfile,
    CAMSnapshot,
    ProjectProfile,
    ReadinessStatus,
)
from app.services.dpr_agentic.finding_consolidation import consolidate
from app.services.dpr_agentic.schemas import AnalysisResult, Finding


def _result(*findings: Finding, status: str = "completed") -> AnalysisResult:
    return AnalysisResult(
        service_request_id="SR-1",
        wallet_document_id="W-1",
        document_sha256=hashlib.sha256(b"sample").hexdigest(),
        status=status,
        agent_outputs={},
        findings=list(findings),
        unresolved_questions=[],
    )


def test_snapshot_round_trips_through_json():
    result = _result(
        Finding(
            category="financial_calculation",
            severity="high",
            title="Cost Of Project Total does not reconcile",
            explanation="Computed 82.00; the document states 80.00 (difference 2.00).",
            suggested_action="Verify with applicant.",
            provenance="calculated",
            verification="source_verified",
        )
    )
    snapshot = build_snapshot_from_analysis(
        result,
        report_id="RPT-1",
        report_version=1,
        analysis_run_id="RUN-1",
        borrower_profile=BorrowerProfile(borrower_name="Test Borrower"),
        project_profile=ProjectProfile(project_name="Test Project"),
    )
    assert snapshot.schema_version == CAM_SNAPSHOT_SCHEMA_VERSION
    dumped = snapshot.model_dump(mode="json")
    restored = CAMSnapshot.model_validate(dumped)
    assert restored == snapshot


def test_no_findings_is_ready_for_submission_review():
    snapshot = build_snapshot_from_analysis(
        _result(), report_id="RPT-2", report_version=1, analysis_run_id="RUN-2"
    )
    assert (
        snapshot.readiness_assessment.status
        == ReadinessStatus.READY_FOR_SUBMISSION_REVIEW
    )
    assert snapshot.findings == []


def test_critical_finding_forces_expert_review():
    result = _result(
        Finding(
            category="financial_calculation",
            severity="critical",
            title="Project cost wildly inconsistent",
            explanation="x",
            suggested_action="Verify.",
        )
    )
    snapshot = build_snapshot_from_analysis(
        result, report_id="RPT-3", report_version=1, analysis_run_id="RUN-3"
    )
    assert (
        snapshot.readiness_assessment.status == ReadinessStatus.EXPERT_REVIEW_REQUIRED
    )
    assert snapshot.readiness_assessment.critical_findings_count == 1


def test_incomplete_analysis_is_insufficient_information():
    snapshot = build_snapshot_from_analysis(
        _result(status="incomplete"),
        report_id="RPT-4",
        report_version=1,
        analysis_run_id="RUN-4",
    )
    assert (
        snapshot.readiness_assessment.status == ReadinessStatus.INSUFFICIENT_INFORMATION
    )


def test_never_invents_borrower_or_project_identity():
    snapshot = build_snapshot_from_analysis(
        _result(), report_id="RPT-5", report_version=1, analysis_run_id="RUN-5"
    )
    assert snapshot.borrower_profile.borrower_name is None
    assert snapshot.project_profile.project_name is None


def test_findings_carry_evidence_page_as_source_pages():
    result = _result(
        Finding(
            category="inconsistency",
            severity="medium",
            title="Mismatched applicant name",
            explanation="x",
            evidence_page=3,
            suggested_action="Verify.",
        )
    )
    snapshot = build_snapshot_from_analysis(
        result, report_id="RPT-6", report_version=1, analysis_run_id="RUN-6"
    )
    assert snapshot.findings[0].source_pages == [3]
    assert snapshot.findings[0].finding_class.value == "document_defect"


def test_no_two_findings_in_a_snapshot_share_a_dedup_key():
    """Section 5.1 point 5: the real pipeline emitted the same issue up to
    seven times. The consolidation pass must collapse that before it ever
    reaches the snapshot."""
    duplicated = [
        Finding(
            category="financial_calculation",
            severity="high",
            title="Cost Of Project Total does not reconcile",
            explanation=f"restated by agent {i}",
            evidence_page=2,
            suggested_action="Verify.",
        )
        for i in range(5)
    ]
    result = _result(*consolidate(duplicated))
    snapshot = build_snapshot_from_analysis(
        result, report_id="RPT-7", report_version=1, analysis_run_id="RUN-7"
    )
    assert len(snapshot.findings) == 1
    assert snapshot.findings[0].merged_duplicate_count == 5
    keys = [f.dedup_key for f in snapshot.findings]
    assert len(keys) == len(set(keys))


def test_cam_engine_outputs_populate_financial_ratios_and_repayment_schedule():
    """Milestone 2b: when the run carried cam_engine_outputs (the engine
    was enabled), the adapter folds them into the snapshot's structured
    financial fields, not just findings."""
    from decimal import Decimal

    from app.services.dpr_agentic.cam_engine_bridge import (
        CamEngineOutputs,
        ScenarioOutput,
    )
    from app.services.dpr_agentic.cam_snapshot import CalculationTrace

    engine_outputs = CamEngineOutputs(
        dscr_pat_dep_interest=CalculationTrace(
            formula="x", computed_value=Decimal("1.97"), status="COMPUTED"
        ),
        breakeven_accounting=CalculationTrace(
            formula="x", computed_value=Decimal("726"), status="COMPUTED"
        ),
        year1_principal_due="3.21",
        year1_interest_due="2.03",
        scenarios=[
            ScenarioOutput(
                scenario_id="dpr_as_written",
                label="As stated",
                is_hypothetical=False,
                source_class="dpr_stated",
                dscr=CalculationTrace(
                    formula="x", computed_value=Decimal("1.97"), status="COMPUTED"
                ),
            )
        ],
    )
    result = _result()
    result = result.model_copy(
        update={"cam_engine_outputs": engine_outputs.model_dump(mode="json")}
    )
    snapshot = build_snapshot_from_analysis(
        result, report_id="RPT-8", report_version=1, analysis_run_id="RUN-8"
    )
    assert snapshot.financial_ratios.average_dscr.computed_value == Decimal("1.97")
    assert snapshot.financial_ratios.breakeven_accounting.computed_value == Decimal(
        "726"
    )
    assert len(snapshot.repayment_schedule) == 1
    assert snapshot.repayment_schedule[0].total_debt_service.amount == Decimal("5.24")
    assert len(snapshot.scenario_analysis) == 1
    assert snapshot.scenario_analysis[0].is_hypothetical is False
