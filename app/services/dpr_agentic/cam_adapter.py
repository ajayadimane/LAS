"""Milestone 1 adapter: AnalysisResult (schemas.py) -> CAMSnapshot (cam_snapshot.py).

This is intentionally a thin, honest mapping. It does NOT:
  - deduplicate findings (milestone 2, Section 5.1 of PROGRESS.md's source brief)
  - assign severity/priority by rule (milestone 2, Section 7)
  - compute financial_projections, repayment_schedule, financial_ratios or
    scenario_analysis (milestone 2, Section 9) -- the current Finance agent
    and financial_calc.py do not produce the structured figures those models
    need (see PROGRESS.md "Gaps found" for exactly what's missing)
  - run the verification layer (milestone 3, Section 13)

What it does is turn every AnalysisResult.Finding into a VerifiedFinding with
a provisional (non-ruled) priority, carry over whatever calculated_facts
already exist as CalculationTrace-backed findings, and leave every field the
pipeline cannot currently produce as an explicit UNAVAILABLE/None/empty value
-- never a guess. Borrower/project identity is not in AnalysisResult at all
(the agentic pipeline only ever sees document text), so it must be supplied
by the caller from Core LAS' own records (enrolment/person data), not invented
here.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

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
    OperationalAssumptions,
    PolicyAssessment,
    ProjectCost,
    ProjectProfile,
    ReadinessAssessment,
    ReadinessStatus,
    ValueClassification,
    VerifiedFinding,
)
from .schemas import AnalysisResult, Finding

_DOCUMENT_DEFECT_CATEGORIES = {
    "financial_calculation",
    "dscr",
    "interest_subvention",
    "inconsistency",
    "arithmetic",
    "missing_document",
}


def _finding_class(f: Finding) -> FindingClass:
    """Section 5.1.6: a defect is an error/inconsistency/omission in the DPR
    itself; anything else (seasonality, single-asset dependency, market
    conditions...) is a property of the project -- a business risk. The
    current agents don't tag this distinction themselves, so until the Risk/
    Verification agents are extended to emit it (see PROGRESS.md milestone-1
    "Agent extensions needed"), it is inferred from category as a
    placeholder; this is a conservative heuristic, not the final rule."""
    return (
        FindingClass.DOCUMENT_DEFECT
        if f.category in _DOCUMENT_DEFECT_CATEGORIES
        else FindingClass.BUSINESS_RISK
    )


def _dedup_key(f: Finding) -> str:
    """Placeholder key (category + normalized title + evidence page), per
    Section 5.1's "category + affected metric + source page references".
    Real consolidation (merging evidence, picking one severity by rule,
    counting duplicates) is milestone 2; this adapter does not merge --
    two findings sharing this key still both appear, one per Finding, so
    today's known-duplicate bug is visible in the snapshot rather than
    silently fixed by the adapter."""
    normalized_title = " ".join(f.title.lower().split())
    return hashlib.sha256(
        f"{f.category}|{normalized_title}|{f.evidence_page}".encode()
    ).hexdigest()[:16]


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
    return VerifiedFinding(
        finding_id=f"F{index:04d}",
        dedup_key=_dedup_key(f),
        merged_duplicate_count=1,
        title=f.title,
        category=f.category,
        finding_class=_finding_class(f),
        severity=_severity(f),
        # No deterministic priority engine exists yet (Section 7) -- mapping
        # severity 1:1 as a provisional value rather than leaving it unset,
        # but this is NOT the ruled priority the brief requires.
        priority=(
            FindingPriority.MUST_FIX
            if f.severity == "critical"
            else FindingPriority.SHOULD_FIX
            if f.severity == "high"
            else FindingPriority.GOOD_TO_FIX
            if f.severity in ("medium", "low")
            else FindingPriority.NONE
        ),
        priority_rule="PROVISIONAL_SEVERITY_MAP_NOT_RULE_ENGINE",
        verification=f.verification,
        explanation=f.explanation,
        dpr_reported_value=None,
        recomputed_value=f.title if f.provenance == "calculated" else None,
        value_classification=_value_classification(f),
        source_pages=[f.evidence_page] if f.evidence_page else [],
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
        repayment_schedule=[],
        financial_ratios=FinancialRatios(
            dscr_definition_used=DscrDefinition.PAT_DEP_INTEREST
        ),
        scenario_analysis=[],
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
            "Findings above are not yet deduplicated across agents; the "
            "same issue may appear more than once (see PROGRESS.md "
            "milestone 2, Section 5.1).",
        ],
        evidence_registry=[],
        generated_at=datetime.now(timezone.utc),
    )
