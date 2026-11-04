"""AnalysisResult (schemas.py) -> CAMSnapshot (cam_snapshot.py) adapter.

`result.findings` is already deduplicated and rule-classified by the time it
reaches here (finding_consolidation.consolidate(), run inside
orchestrator.analyze()/parallel_graph.analyze_parallel() -- see Finding's
dedup_key/merged_duplicate_count/finding_class/priority/priority_rule
fields). This module only reshapes an already-consolidated Finding into a
VerifiedFinding; it does not dedupe or classify anything itself. A fallback
to finding_rules' own classifiers is kept for a Finding persisted before
those fields existed (report_store.py keeps old rows as-is -- see
PROGRESS.md Section 16 gap on report versioning).

Still NOT done here (milestone 2's remaining scope, Section 9):
  - project_cost / means_of_finance reconciliation
  - financial_projections, repayment_schedule, financial_ratios, scenario_analysis
  - the verification layer (milestone 3, Section 13)

Borrower/project identity is not in AnalysisResult at all (the agentic
pipeline only ever sees document text), so it must be supplied by the caller
from Core LAS' own records (enrolment/person data), not invented here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from .cam_snapshot import (
    BorrowerProfile,
    CAMSnapshot,
    DocumentReference,
    DscrDefinition,
    FinancialRatios,
    FindingClass,
    FindingPriority,
    FindingSeverity,
    MeansOfFinance,
    Money,
    OperationalAssumptions,
    PolicyAssessment,
    ProjectCost,
    ProjectProfile,
    ReadinessAssessment,
    ReadinessStatus,
    RepaymentPeriod,
    ScenarioAnalysis,
    ValueClassification,
    VerifiedFinding,
)
from .finding_rules import (
    classify_finding_class,
    compute_dedup_key,
    priority_for_narrative,
)
from .schemas import AnalysisResult, Finding


def _engine_derived_fields(
    result: AnalysisResult,
) -> tuple[FinancialRatios, list[RepaymentPeriod], list[ScenarioAnalysis]]:
    """Folds cam_engine_bridge.CamEngineOutputs (set only when
    Settings.cam_engine_enabled was True for this run -- milestone 2b) into
    the snapshot's financial_ratios/repayment_schedule/scenario_analysis.
    Returns empty/default values when the engine didn't run for this
    result, exactly as before this wiring existed.

    Still NOT populated: project_cost / means_of_finance (the engine's
    outputs don't carry the raw loan/means-of-finance figures themselves,
    only derived traces -- see PROGRESS.md milestone 2b for the exact gap).
    """
    default_ratios = FinancialRatios(
        dscr_definition_used=DscrDefinition.PAT_DEP_INTEREST
    )
    if not result.cam_engine_outputs:
        return default_ratios, [], []

    from .cam_engine_bridge import CamEngineOutputs

    engine = CamEngineOutputs.model_validate(result.cam_engine_outputs)
    ratios = FinancialRatios(
        dscr_definition_used=DscrDefinition.PAT_DEP_INTEREST,
        average_dscr=engine.dscr_pat_dep_interest,
        dscr_pat_dep_only=engine.dscr_pat_dep,
        breakeven_accounting=engine.breakeven_accounting,
        breakeven_operating_cashflow=engine.breakeven_operating_cashflow,
    )

    repayment_schedule: list[RepaymentPeriod] = []
    if engine.year1_principal_due is not None and engine.year1_interest_due is not None:
        principal = Decimal(engine.year1_principal_due)
        interest = Decimal(engine.year1_interest_due)
        repayment_schedule.append(
            RepaymentPeriod(
                year=1,
                principal_due=Money(amount=principal, unit="lakh"),
                interest_due=Money(amount=interest, unit="lakh"),
                total_debt_service=Money(amount=principal + interest, unit="lakh"),
                dscr=engine.dscr_pat_dep_interest,
            )
        )

    scenario_analysis = [
        ScenarioAnalysis(
            scenario_id=s.scenario_id,
            label=s.label,
            is_hypothetical=s.is_hypothetical,
            description=f"Source class: {s.source_class}.",
            dscr_by_year={1: s.dscr},
        )
        for s in engine.scenarios
    ]
    return ratios, repayment_schedule, scenario_analysis


def _value_classification(f: Finding) -> ValueClassification:
    return {
        "calculated": ValueClassification.RECOMPUTED,
        "extracted": ValueClassification.VERIFIED_SOURCE_FACT,
        "assumption": ValueClassification.UNVERIFIED_ASSUMPTION,
        "ai_interpreted": ValueClassification.UNVERIFIED_ASSUMPTION,
        "unverified": ValueClassification.UNVERIFIED_ASSUMPTION,
    }.get(f.provenance, ValueClassification.UNVERIFIED_ASSUMPTION)


def _severity(f: Finding) -> FindingSeverity:
    return FindingSeverity(f.severity)


def _map_finding(f: Finding, index: int) -> VerifiedFinding:
    finding_class = f.finding_class or classify_finding_class(f)
    if f.priority:
        priority, priority_rule = f.priority, f.priority_rule or ""
    else:
        priority, priority_rule = priority_for_narrative(f)
    return VerifiedFinding(
        finding_id=f"F{index:04d}",
        dedup_key=f.dedup_key or compute_dedup_key(f),
        merged_duplicate_count=f.merged_duplicate_count,
        title=f.title,
        category=f.category,
        finding_class=FindingClass(finding_class),
        severity=_severity(f),
        priority=FindingPriority(priority),
        priority_rule=priority_rule,
        verification=f.verification,
        explanation=f.explanation,
        dpr_reported_value=f.dpr_stated_value,
        recomputed_value=f.recomputed_display_value,
        value_classification=_value_classification(f),
        source_pages=[f.evidence_page] if f.evidence_page is not None else [],
        evidence=[],
        suggested_action=f.suggested_action,
        responsible_person=None,
        resolution_status="open",
        requires_borrower_confirmation=f.verification == "needs_clarification",
    )


def build_snapshot_from_analysis(
    result: AnalysisResult,
    *,
    report_id: str,
    report_version: int,
    analysis_run_id: str,
    borrower_profile: BorrowerProfile | None = None,
    project_profile: ProjectProfile | None = None,
) -> CAMSnapshot:
    """Best-effort milestone-1 mapping. `borrower_profile`/`project_profile`
    must come from the caller's own Core LAS/Wallet-sourced data (e.g.
    report_service.py's enrolment lookup) -- this adapter never invents them.
    """
    findings = [_map_finding(f, i) for i, f in enumerate(result.findings, start=1)]
    critical_count = sum(1 for f in findings if f.severity == FindingSeverity.CRITICAL)
    financial_ratios, repayment_schedule, scenario_analysis = _engine_derived_fields(
        result
    )

    status = (
        ReadinessStatus.INSUFFICIENT_INFORMATION
        if result.status != "completed"
        else ReadinessStatus.EXPERT_REVIEW_REQUIRED
        if critical_count
        else ReadinessStatus.CORRECTIONS_REQUIRED
        if findings
        else ReadinessStatus.READY_FOR_SUBMISSION_REVIEW
    )

    return CAMSnapshot(
        report_id=report_id,
        report_version=report_version,
        analysis_run_id=analysis_run_id,
        service_request_id=result.service_request_id,
        borrower_profile=borrower_profile or BorrowerProfile(),
        project_profile=project_profile or ProjectProfile(),
        document_inventory=[
            DocumentReference(
                wallet_document_id=result.wallet_document_id,
                document_sha256=result.document_sha256,
            )
        ],
        project_cost=ProjectCost(),
        means_of_finance=MeansOfFinance(),
        operational_assumptions=OperationalAssumptions(),
        financial_projections=[],
        repayment_schedule=repayment_schedule,
        financial_ratios=financial_ratios,
        scenario_analysis=scenario_analysis,
        findings=findings,
        proposed_corrections=[],
        policy_assessment=PolicyAssessment(),
        readiness_assessment=ReadinessAssessment(
            status=status,
            critical_findings_count=critical_count,
            outstanding_clarifications_count=len(result.unresolved_questions),
        ),
        report_limitations=[
            "Project cost, means-of-finance reconciliation, financial "
            "projections, repayment schedule, DSCR, scenario analysis and "
            "policy assessment are not yet populated by this pipeline "
            "(see PROGRESS.md milestone 2).",
            "Findings above are deduplicated by category, affected metric "
            "and source page; a finding's merged_duplicate_count shows how "
            "many agent outputs raised it. Priority for findings without a "
            "code-verified calculation behind them is a severity-based "
            "fallback, not a measured financial-impact percentage (see "
            "PROGRESS.md milestone 2).",
        ],
        evidence_registry=[],
        generated_at=datetime.now(timezone.utc),
    )
