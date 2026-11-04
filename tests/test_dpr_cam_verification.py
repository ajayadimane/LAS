"""Milestone 3: the Section 13 verification layer."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.dpr_agentic.cam_adapter import build_snapshot_from_analysis
from app.services.dpr_agentic.cam_snapshot import (
    CalculationTrace,
    CAMSnapshot,
    DscrDefinition,
    EvidenceReference,
    FinancialRatios,
    MeansOfFinance,
    Money,
    PolicyAssessment,
    PolicyStatement,
    ProjectCost,
    ReadinessAssessment,
    ReadinessStatus,
    ScenarioAnalysis,
    SubsidyBenefit,
)
from app.services.dpr_agentic.cam_verification import verify_snapshot
from app.services.dpr_agentic.schemas import AnalysisResult


def _base_result() -> AnalysisResult:
    return AnalysisResult(
        service_request_id="SR-1",
        wallet_document_id="W-1",
        document_sha256="a" * 64,
        status="completed",
        agent_outputs={},
        findings=[],
        unresolved_questions=[],
    )


def _base_snapshot(**overrides) -> CAMSnapshot:
    snapshot = build_snapshot_from_analysis(
        _base_result(), report_id="RPT-1", report_version=1, analysis_run_id="RUN-1"
    )
    return snapshot.model_copy(update=overrides)


def test_a_clean_snapshot_is_publishable():
    result = verify_snapshot(_base_snapshot())
    assert result.is_publishable, result.issues


def test_1_required_schema_field_blank_is_blocking():
    snapshot = _base_snapshot(report_id="  ")
    result = verify_snapshot(snapshot)
    assert not result.is_publishable
    assert any(
        i.code == "REQUIRED_SCHEMA_FIELD_MISSING" for i in result.blocking_issues
    )


def test_3_project_cost_funding_mismatch_is_blocking():
    snapshot = _base_snapshot(
        project_cost=ProjectCost(dpr_reported_total=Money(amount=Decimal("30.00"))),
        means_of_finance=MeansOfFinance(
            requested_loan=Money(amount=Decimal("20.00")),
            promoter_contribution=Money(amount=Decimal("5.00")),
        ),
    )
    result = verify_snapshot(snapshot)
    assert any(
        i.code == "PROJECT_COST_FUNDING_MISMATCH" for i in result.blocking_issues
    )


def test_3_project_cost_matching_funding_is_not_flagged():
    snapshot = _base_snapshot(
        project_cost=ProjectCost(dpr_reported_total=Money(amount=Decimal("30.00"))),
        means_of_finance=MeansOfFinance(
            requested_loan=Money(amount=Decimal("22.50")),
            promoter_contribution=Money(amount=Decimal("7.50")),
        ),
    )
    result = verify_snapshot(snapshot)
    assert not any(i.code == "PROJECT_COST_FUNDING_MISMATCH" for i in result.issues)


def test_4_evidence_citing_an_unknown_document_is_blocking():
    snapshot = _base_snapshot(
        evidence_registry=[
            EvidenceReference(
                evidence_id="E1",
                source_document_id="W-does-not-exist",
                evidence_class="source_verified",
            )
        ]
    )
    result = verify_snapshot(snapshot)
    assert any(
        i.code == "SOURCE_REFERENCE_DOES_NOT_EXIST" for i in result.blocking_issues
    )


def test_5_invalid_page_number_is_blocking():
    from app.services.dpr_agentic.schemas import Finding

    result = AnalysisResult(
        service_request_id="SR-1",
        wallet_document_id="W-1",
        document_sha256="a" * 64,
        status="completed",
        agent_outputs={},
        findings=[
            Finding(
                category="inconsistency",
                severity="medium",
                title="x",
                explanation="x",
                evidence_page=0,
                suggested_action="x",
            )
        ],
        unresolved_questions=[],
    )
    snapshot = build_snapshot_from_analysis(
        result, report_id="RPT-3", report_version=1, analysis_run_id="RUN-3"
    )
    verification = verify_snapshot(snapshot)
    assert any(i.code == "INVALID_PAGE_CITATION" for i in verification.blocking_issues)


def test_6_computed_trace_without_a_value_is_blocking():
    snapshot = _base_snapshot(
        financial_ratios=FinancialRatios(
            dscr_definition_used=DscrDefinition.PAT_DEP_INTEREST,
            average_dscr=CalculationTrace(
                formula="x / y", status="COMPUTED", computed_value=None
            ),
        )
    )
    result = verify_snapshot(snapshot)
    assert any(i.code == "CALCULATION_NOT_REPRODUCIBLE" for i in result.blocking_issues)


def test_6_not_computable_trace_without_a_reason_is_blocking():
    snapshot = _base_snapshot(
        financial_ratios=FinancialRatios(
            dscr_definition_used=DscrDefinition.PAT_DEP_INTEREST,
            average_dscr=CalculationTrace(formula="x / y", status="NOT_COMPUTABLE"),
        )
    )
    result = verify_snapshot(snapshot)
    assert any(i.code == "CALCULATION_NOT_REPRODUCIBLE" for i in result.blocking_issues)


def test_6_valid_calculation_trace_is_not_flagged():
    snapshot = _base_snapshot(
        financial_ratios=FinancialRatios(
            dscr_definition_used=DscrDefinition.PAT_DEP_INTEREST,
            average_dscr=CalculationTrace(
                formula="CADS / Debt service",
                status="COMPUTED",
                computed_value=Decimal("1.5"),
            ),
        )
    )
    result = verify_snapshot(snapshot)
    assert not any(i.code == "CALCULATION_NOT_REPRODUCIBLE" for i in result.issues)


def test_7_unlabeled_scenario_is_blocking():
    snapshot = _base_snapshot(
        scenario_analysis=[
            ScenarioAnalysis(
                scenario_id="s1", label="", is_hypothetical=True, description="x"
            )
        ]
    )
    result = verify_snapshot(snapshot)
    assert any(i.code == "SCENARIO_NOT_LABELED" for i in result.blocking_issues)


def test_7_two_non_hypothetical_scenarios_is_a_warning():
    snapshot = _base_snapshot(
        scenario_analysis=[
            ScenarioAnalysis(
                scenario_id="s1", label="A", is_hypothetical=False, description="x"
            ),
            ScenarioAnalysis(
                scenario_id="s2", label="B", is_hypothetical=False, description="y"
            ),
        ]
    )
    result = verify_snapshot(snapshot)
    assert result.is_publishable  # warning, not blocking
    assert any(i.code == "SCENARIO_FACT_AMBIGUITY" for i in result.warnings)


def test_8_verified_policy_requirement_without_a_source_is_blocking():
    snapshot = _base_snapshot(
        policy_assessment=PolicyAssessment(
            statements=[
                PolicyStatement(
                    text="DSCR must be >= 1.25x", status="verified_policy_requirement"
                )
            ]
        )
    )
    result = verify_snapshot(snapshot)
    assert any(
        i.code == "UNVERIFIED_POLICY_CLAIMED_AS_REQUIREMENT"
        for i in result.blocking_issues
    )


def test_8_verified_policy_requirement_with_a_dated_source_is_not_flagged():
    snapshot = _base_snapshot(
        policy_assessment=PolicyAssessment(
            statements=[
                PolicyStatement(
                    text="DSCR must be >= 1.25x",
                    status="verified_policy_requirement",
                    source_url="https://example.sbi/policy",
                    effective_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                )
            ]
        )
    )
    result = verify_snapshot(snapshot)
    assert not any(
        i.code == "UNVERIFIED_POLICY_CLAIMED_AS_REQUIREMENT" for i in result.issues
    )


def test_9_critical_finding_with_wrong_readiness_status_is_blocking():
    from app.services.dpr_agentic.schemas import Finding

    result = AnalysisResult(
        service_request_id="SR-1",
        wallet_document_id="W-1",
        document_sha256="a" * 64,
        status="completed",
        agent_outputs={},
        findings=[
            Finding(
                category="financial_calculation",
                severity="critical",
                title="x",
                explanation="x",
                suggested_action="x",
            )
        ],
        unresolved_questions=[],
    )
    snapshot = build_snapshot_from_analysis(
        result, report_id="RPT-4", report_version=1, analysis_run_id="RUN-4"
    )
    # Tamper with the readiness status the adapter correctly computed, to
    # simulate a bug that produced an inconsistent snapshot.
    tampered = snapshot.model_copy(
        update={
            "readiness_assessment": snapshot.readiness_assessment.model_copy(
                update={"status": ReadinessStatus.READY_FOR_SUBMISSION_REVIEW}
            )
        }
    )
    verification = verify_snapshot(tampered)
    assert any(
        i.code == "READINESS_STATUS_INCONSISTENT_WITH_FINDINGS"
        for i in verification.blocking_issues
    )


def test_9_adapter_output_is_internally_consistent():
    """The adapter itself must never produce a snapshot this layer rejects
    on readiness grounds -- a regression here means milestone 1/2's adapter
    logic drifted from milestone 3's consistency rule."""
    from app.services.dpr_agentic.schemas import Finding

    result = AnalysisResult(
        service_request_id="SR-1",
        wallet_document_id="W-1",
        document_sha256="a" * 64,
        status="completed",
        agent_outputs={},
        findings=[
            Finding(
                category="financial_calculation",
                severity="critical",
                title="x",
                explanation="x",
                suggested_action="x",
            )
        ],
        unresolved_questions=[],
    )
    snapshot = build_snapshot_from_analysis(
        result, report_id="RPT-5", report_version=1, analysis_run_id="RUN-5"
    )
    verification = verify_snapshot(snapshot)
    assert not any(
        i.code
        in (
            "READINESS_STATUS_INCONSISTENT_WITH_FINDINGS",
            "READINESS_CRITICAL_COUNT_MISMATCH",
        )
        for i in verification.blocking_issues
    )


def test_10_verified_subsidy_without_a_source_is_blocking():
    snapshot = _base_snapshot(
        readiness_assessment=ReadinessAssessment(
            status=ReadinessStatus.READY_FOR_SUBMISSION_REVIEW,
            subsidy_benefits=[
                SubsidyBenefit(scheme_name="AIF", eligibility_status="verified")
            ],
        )
    )
    result = verify_snapshot(snapshot)
    assert any(i.code == "UNSUPPORTED_SUBSIDY_CLAIM" for i in result.blocking_issues)


def test_10_conditional_subsidy_without_a_source_is_not_flagged():
    snapshot = _base_snapshot(
        readiness_assessment=ReadinessAssessment(
            status=ReadinessStatus.READY_FOR_SUBMISSION_REVIEW,
            subsidy_benefits=[
                SubsidyBenefit(scheme_name="AIF", eligibility_status="conditional")
            ],
        )
    )
    result = verify_snapshot(snapshot)
    assert not any(i.code == "UNSUPPORTED_SUBSIDY_CLAIM" for i in result.issues)


def test_11_unresolved_jinja_placeholder_is_blocking():
    snapshot = _base_snapshot(
        report_limitations=["{{o.badge}} could not be resolved for this borrower."]
    )
    result = verify_snapshot(snapshot)
    assert any(
        i.code == "UNRESOLVED_TEMPLATE_PLACEHOLDER" for i in result.blocking_issues
    )


def test_11_literal_sample_placeholder_is_blocking():
    """Section 18's own template test list: this literal string ships in
    the sample CAM PDF and must never reach a real rendered report."""
    from app.services.dpr_agentic.cam_snapshot import BorrowerProfile

    snapshot = _base_snapshot(
        borrower_profile=BorrowerProfile(borrower_name="[Customer full name]")
    )
    result = verify_snapshot(snapshot)
    assert any(
        i.code == "UNRESOLVED_TEMPLATE_PLACEHOLDER" for i in result.blocking_issues
    )


def test_12_missing_documents_without_insufficient_information_status_is_blocking():
    snapshot = _base_snapshot(document_inventory=[])
    result = verify_snapshot(snapshot)
    assert any(i.code == "REPORT_INCOMPLETE" for i in result.blocking_issues)


def test_12_future_generated_at_is_blocking():
    snapshot = _base_snapshot(
        generated_at=datetime.now(timezone.utc) + timedelta(days=1)
    )
    result = verify_snapshot(snapshot)
    assert any(
        i.code == "REPORT_GENERATED_AT_IN_FUTURE" for i in result.blocking_issues
    )


def test_13_duplicate_dedup_key_is_blocking():
    from app.services.dpr_agentic.cam_snapshot import (
        FindingClass,
        FindingPriority,
        FindingSeverity,
        ValueClassification,
        VerifiedFinding,
    )

    f1 = VerifiedFinding(
        finding_id="F0001",
        dedup_key="same-key",
        title="A",
        category="financial_calculation",
        finding_class=FindingClass.DOCUMENT_DEFECT,
        severity=FindingSeverity.HIGH,
        priority=FindingPriority.SHOULD_FIX,
        priority_rule="x",
        verification="unverified",
        explanation="x",
        value_classification=ValueClassification.RECOMPUTED,
        suggested_action="x",
    )
    f2 = f1.model_copy(update={"finding_id": "F0002", "title": "B"})
    snapshot = _base_snapshot(findings=[f1, f2])
    result = verify_snapshot(snapshot)
    assert any(i.code == "DUPLICATE_FINDING_DEDUP_KEY" for i in result.blocking_issues)
    assert not result.is_publishable
