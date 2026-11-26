"""The canonical CAM (Credit Appraisal Memo) data snapshot.

This is the single source of truth for every value shown on the six-page
borrower report and the CAM annexures (see cam-design-reference/ and
PROGRESS.md). Nothing downstream of cam_adapter.build_snapshot() may ask an
LLM to re-derive a number: every page and annexure reads from one frozen
CAMSnapshot instance.

Every amount is Decimal, carries its own currency/unit, and (where it comes
from a calculation) a provenance trail back to the inputs and formula that
produced it -- see Provenance/CalculationTrace. A field that cannot be
derived from the current agent pipeline is left None/empty rather than
guessed; callers must render an explicit "requires verification" /
"not available" state for it (Section 6.2/19 of the brief: never invent a
missing value).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

# 2.0.0: the management-approved five-page "DPR Pre-submission Review"
# layout (cam_render_v2.py) and the fields it prints. 1.x snapshots keep
# the original six-page layout, so an issued report never changes.
CAM_SNAPSHOT_SCHEMA_VERSION = "2.0.0"

Currency = Literal["INR"]


# --------------------------------------------------------------------------
# Shared value types
# --------------------------------------------------------------------------


class Provenance(str, Enum):
    """How a value came to be in the snapshot. Mirrors Finding.provenance
    (schemas.py) but is also used for non-finding values such as a project
    cost figure or a ratio, so it is its own enum rather than a re-export.
    """

    SOURCE_VERIFIED = "source_verified"  # copied verbatim from the DPR, quote checked
    EXTRACTED = "extracted"  # copied verbatim, quote not (yet) position-checked
    CALCULATED = "calculated"  # produced by financial_calc.py / cam_financials.py
    AI_INTERPRETED = "ai_interpreted"  # an agent's narrative judgment
    ASSUMPTION = "assumption"  # an explicitly labeled illustrative assumption
    EXTERNAL_BENCHMARK = "external_benchmark"  # a cited external figure
    UNVERIFIED = "unverified"
    UNAVAILABLE = "unavailable"  # the pipeline has no value for this field at all


class Money(BaseModel):
    amount: Decimal
    currency: Currency = "INR"
    unit: Literal["absolute", "lakh", "crore"] = "absolute"


class CalculationTrace(BaseModel):
    """Reproducibility record for one computed figure (Section 9/13)."""

    formula: str
    inputs: dict[str, str] = Field(default_factory=dict)
    computed_value: Decimal | None = None
    status: Literal["COMPUTED", "NOT_COMPUTABLE"] = "COMPUTED"
    reason_not_computable: str | None = None
    calculation_version: str = "1.0.0"


class EvidenceReference(BaseModel):
    evidence_id: str
    source_document_id: str  # wallet_document_id
    page: int | None = None
    quote: str | None = None
    evidence_class: Literal[
        "source_verified",
        "recomputed",
        "external_benchmark",
        "assumption",
        "unverified",
    ]


# --------------------------------------------------------------------------
# Borrower / project
# --------------------------------------------------------------------------


class BorrowerProfile(BaseModel):
    borrower_name: str | None = None
    aadhaar_last4: str | None = None  # never the full number (Section 19.21)
    mobile_number: str | None = None
    customer_person_id: str | None = None
    bd_broker_id: str | None = None
    bd_name: str | None = None


class ProjectProfile(BaseModel):
    project_name: str | None = None
    business_sector: str | None = None
    business_subsector: str | None = None
    project_location: str | None = None
    project_purpose: str | None = None
    scheme_claimed: str | None = None
    machinery_equipment: list[str] = Field(default_factory=list)
    # The bank the analysis is for: the DPR's own verified statement, else
    # the BankName service detail, else DPR_DEFAULT_LENDING_BANK
    # (grounding.resolve_lending_bank).
    lending_bank: str | None = None
    lending_bank_source: Literal["dpr", "dashboard", "default"] | None = None


class DocumentReference(BaseModel):
    wallet_document_id: str
    document_sha256: str
    document_class: str | None = None
    page_count: int | None = None


# --------------------------------------------------------------------------
# Financials
# --------------------------------------------------------------------------


class ProjectCostLine(BaseModel):
    label: str
    dpr_reported: Money | None = None
    provenance: Provenance = Provenance.UNAVAILABLE


class ProjectCost(BaseModel):
    components: list[ProjectCostLine] = Field(default_factory=list)
    dpr_reported_total: Money | None = None
    recomputed_total: Money | None = None
    reconciliation: CalculationTrace | None = None


class FinanceSourceClass(str, Enum):
    UPFRONT_OWN_FUNDED = "upfront_own_funded"
    UPFRONT_LOAN = "upfront_loan"
    CONDITIONAL_SUBSIDY = "conditional_subsidy"
    BACK_ENDED_SUBSIDY = "back_ended_subsidy"
    REIMBURSEMENT_SCHEME = "reimbursement_scheme"
    FUTURE_CASH_INFLOW = "future_cash_inflow"


class MeansOfFinanceLine(BaseModel):
    label: str
    amount: Money | None = None
    source_class: FinanceSourceClass
    counted_in_upfront_funding: bool


class MeansOfFinance(BaseModel):
    components: list[MeansOfFinanceLine] = Field(default_factory=list)
    dpr_reported_total: Money | None = None
    upfront_funding_total: Money | None = None  # excludes back-ended/conditional/future
    funding_gap: CalculationTrace | None = None
    requested_loan: Money | None = None
    promoter_contribution: Money | None = None
    proposed_tenure_months: int | None = None


class OperationalAssumptions(BaseModel):
    """Sector-specific technical-feasibility assumptions (Section 8). Free-form
    per sector -- the agents select which keys apply; this is not a fixed
    harvester/dairy/polyhouse schema so a new sector needs no model change."""

    sector: str | None = None
    assumptions: dict[str, str] = Field(default_factory=dict)
    classification: Literal[
        "verified_source_fact",
        "recomputed",
        "external_benchmark",
        "illustrative_scenario",
        "unverified_assumption",
    ] = "unverified_assumption"


class FinancialProjection(BaseModel):
    year: int
    revenue: Money | None = None
    operating_expenses: Money | None = None
    net_profit: Money | None = None
    depreciation: Money | None = None
    interest_on_term_loan: Money | None = None
    provenance: Provenance = Provenance.UNAVAILABLE


class RepaymentPeriod(BaseModel):
    year: int
    principal_due: Money | None = None
    interest_due: Money | None = None
    total_debt_service: Money | None = None
    cash_available_for_debt_service: Money | None = None
    dscr: CalculationTrace | None = None
    dscr_stated_in_dpr: Decimal | None = None


class DscrDefinition(str, Enum):
    PAT_DEP_INTEREST = "pat_dep_interest"  # PAT + Depreciation + Interest on term loan
    PAT_DEP = "pat_dep"  # PAT + Depreciation


class FinancialRatios(BaseModel):
    dscr_definition_used: DscrDefinition
    # Reports frozen before 2026-11-25 hold the first repaying year's DSCR
    # here under the wrong name; newer ones use `first_year_dscr` and leave
    # this empty until a real multi-year average is computed.
    average_dscr: CalculationTrace | None = None
    # DSCR of the first year that repays principal (standard formula).
    first_year_dscr: CalculationTrace | None = None
    # The document's own convention (PAT + Depreciation only, no interest
    # added back) -- shown alongside `first_year_dscr` (the standard formula)
    # so a reader can see why the two differ, never to replace it.
    dscr_pat_dep_only: CalculationTrace | None = None
    breakeven_accounting: CalculationTrace | None = None
    breakeven_operating_cashflow: CalculationTrace | None = None
    breakeven_debt_service_threshold: CalculationTrace | None = None


class ScenarioAnalysis(BaseModel):
    scenario_id: str
    label: str
    is_hypothetical: bool
    description: str
    dscr_by_year: dict[int, CalculationTrace] = Field(default_factory=dict)
    # Break-even under this scenario's own cost assumption (Section 9/19.7
    # -- never mixes one scenario's costs into another's trace). None when
    # the engine couldn't compute break-even for this scenario (e.g. a
    # non-harvester sector, or a missing running-cost figure).
    breakeven_accounting: CalculationTrace | None = None
    breakeven_operating_cashflow: CalculationTrace | None = None
    breakeven_debt_service_threshold: CalculationTrace | None = None


# --------------------------------------------------------------------------
# Findings and corrections
# --------------------------------------------------------------------------


class FindingSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingPriority(str, Enum):
    MUST_FIX = "must_fix"
    SHOULD_FIX = "should_fix"
    GOOD_TO_FIX = "good_to_fix"
    NONE = "none"  # business risks / info-only findings carry no fix priority


class FindingClass(str, Enum):
    DOCUMENT_DEFECT = "document_defect"  # Page 2
    BUSINESS_RISK = "business_risk"  # Risk section / Annexure E


class ValueClassification(str, Enum):
    VERIFIED_SOURCE_FACT = "verified_source_fact"
    RECOMPUTED = "recomputed"
    EXTERNAL_BENCHMARK = "external_benchmark"
    ILLUSTRATIVE_SCENARIO = "illustrative_scenario"
    UNVERIFIED_ASSUMPTION = "unverified_assumption"


class VerifiedFinding(BaseModel):
    finding_id: str
    dedup_key: str
    merged_duplicate_count: int = 1
    title: str
    category: str
    finding_class: FindingClass
    severity: FindingSeverity
    priority: FindingPriority
    priority_rule: str  # which configured rule fired (Section 7)
    verification: Literal["source_verified", "unverified", "needs_clarification"]
    explanation: str
    dpr_reported_value: str | None = None
    recomputed_value: str | None = None
    value_classification: ValueClassification
    source_pages: list[int] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    suggested_action: str
    responsible_person: Literal["borrower", "bd", "consultant", "bank"] | None = None
    resolution_status: Literal[
        "open", "borrower_confirmed", "corrected", "rejected"
    ] = "open"
    requires_borrower_confirmation: bool = False
    # Short "why it matters" line and target value for the action plan
    # (page 3 of the 2.x layout). None: the renderer falls back by category.
    why_it_matters: str | None = None
    should_be: str | None = None


class CorrectionProposal(BaseModel):
    correction_id: str
    finding_id: str
    original_value: str | None = None
    proposed_value: str | None = None
    basis: Literal[
        "verified_source_evidence",
        "reproducible_calculation",
        "confirmed_borrower_information",
        "documented_external_benchmark",
    ]
    downstream_effects: list[str] = Field(default_factory=list)
    status: Literal["proposed", "authorized", "applied", "rejected"] = "proposed"
    reviewer: str | None = None
    reviewed_at: datetime | None = None


# --------------------------------------------------------------------------
# Policy / security / readiness / benefits
# --------------------------------------------------------------------------


class PolicyStatementStatus(str, Enum):
    VERIFIED_POLICY_REQUIREMENT = "verified_policy_requirement"
    DOCUMENTED_DPR_CONDITION = "documented_dpr_condition"
    BANK_CONFIRMATION_PENDING = "bank_confirmation_pending"
    RECOMMENDED_GOOD_PRACTICE = "recommended_good_practice"


class PolicyStatement(BaseModel):
    text: str
    status: PolicyStatementStatus
    source_url: str | None = None
    # The site the statement came from (e.g. "sbi.co.in"); source_url is
    # Google's grounding redirect, which does not show it.
    source_domain: str | None = None
    effective_date: datetime | None = None
    retrieval_date: datetime | None = None


DOCUMENTS_NOT_VERIFIED = (
    "KYC: identity and address proof, PAN",
    "Land or premises records",
    "Credit bureau report",
)


class DocumentVerificationNotice(BaseModel):
    """What this analysis does not verify, and who does: the borrower's
    documents are checked by the regulated entity under one of our services,
    which collects them and charges for it. Frozen into the snapshot with
    the fee at the time, so a later fee change never rewrites an issued
    report."""

    provider: str
    service_name: str
    service_fee_inr: Decimal | None = None
    documents: list[str] = Field(default_factory=lambda: list(DOCUMENTS_NOT_VERIFIED))
    # The vendor quotation is validated by our own service, not the
    # provider (IFPL does not cover it).
    quotation_service_name: str | None = None
    quotation_service_fee_inr: Decimal | None = None


class PolicyAssessment(BaseModel):
    statements: list[PolicyStatement] = Field(default_factory=list)
    unverified_policy_areas: list[str] = Field(default_factory=list)


class SubsidyBenefit(BaseModel):
    scheme_name: str
    eligibility_status: Literal["verified", "conditional", "unverified"]
    eligible_amount: Money | None = None
    benefit_rate_pct: Decimal | None = None
    effective_period: str | None = None
    outstanding_loan_assumption: str | None = None
    source: str | None = None
    source_effective_date: datetime | None = None
    expected_timing: str | None = None


class ReadinessStatus(str, Enum):
    READY_FOR_SUBMISSION_REVIEW = "READY_FOR_SUBMISSION_REVIEW"
    CORRECTIONS_REQUIRED = "CORRECTIONS_REQUIRED"
    EXPERT_REVIEW_REQUIRED = "EXPERT_REVIEW_REQUIRED"
    INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"


class ReadinessDimensionScore(BaseModel):
    dimension: Literal[
        "arithmetic_accuracy",
        "internal_consistency",
        "assumption_realism",
        "completeness",
    ]
    score: int = Field(ge=0, le=25)
    findings_applied: list[str] = Field(default_factory=list)  # finding_ids


class ReadinessAssessment(BaseModel):
    status: ReadinessStatus
    # "checks": total_score is the share of checks passed (checks_passed of
    # checks_total), the 2.x cover's readiness circle.
    score_mode: Literal["status", "dimensions", "checks"] = "status"
    total_score: int | None = Field(default=None, ge=0, le=100)
    checks_passed: int | None = None
    checks_total: int | None = None
    dimension_scores: list[ReadinessDimensionScore] = Field(default_factory=list)
    critical_findings_count: int = 0
    outstanding_clarifications_count: int = 0
    subsidy_benefits: list[SubsidyBenefit] = Field(default_factory=list)


class ReviewMeasure(BaseModel):
    """One row of "Your figures, recomputed" (2.x page 4)."""

    measure: str
    dpr_value: str | None = None
    recomputed: str | None = None
    baseline: str
    status: Literal["meets", "below", "pending"] = "pending"


class AssumptionRow(BaseModel):
    """One row of "Assumptions to revise" (2.x page 4): the DPR's figure,
    the reference it was compared with, and the figure we suggest."""

    assumption: str
    dpr_value: str | None = None
    reference: str | None = None
    suggested: str | None = None
    flagged: bool = False


class DocumentStatus(BaseModel):
    """One row of "Documents to prepare" (2.x page 5)."""

    document: str
    status: Literal["available", "missing", "update", "to_confirm"] = "to_confirm"
    action: str | None = None


class SchemeColumn(BaseModel):
    """One scheme's column in "Are you applying under the right scheme?"
    Display strings; None prints as "—"."""

    name: str
    # Never "eligible": a rules table cannot certify that (the bank does).
    eligible: Literal["likely", "partly", "no", "not_assessed"] = "not_assessed"
    cost_ceiling: str | None = None
    contribution: str | None = None
    capital_subsidy: str | None = None
    interest_subvention: str | None = None
    credit_guarantee: str | None = None
    tenure_moratorium: str | None = None
    combinable: str | None = None
    total_benefit: str | None = None


class SchemeGap(BaseModel):
    criterion: str
    requires: str
    dpr_shows: str
    action: str


class SchemeReview(BaseModel):
    chosen: SchemeColumn | None = None
    recommended: SchemeColumn | None = None
    alternative: SchemeColumn | None = None
    verdict: Literal["correct", "better_available", "not_eligible", "not_assessed"] = (
        "not_assessed"
    )
    recommendation: str = ""
    gaps: list[SchemeGap] = Field(default_factory=list)


class StressTest(BaseModel):
    """"How the bank will stress-test this" (2.x page 4)."""

    price_fall_pct: int = 10
    average_dscr: Decimal | None = None
    minimum_dscr: Decimal | None = None
    minimum_year: str | None = None
    most_sensitive: str | None = None


class KycVerification(BaseModel):
    """Credit and KYC results from the regulated entity (IFPL, Value
    Addition service), received through its API. Statuses only -- never a
    PAN or Aadhaar number."""

    provider: str = "IFPL"
    consent_date: datetime | None = None
    credit_score: int | None = None
    credit_band: Literal["good", "fair", "poor"] | None = None
    overdues: str | None = None
    write_offs: str | None = None
    pan: Literal["verified", "mismatch"] | None = None
    aadhaar: Literal["verified", "mismatch"] | None = None
    address: Literal["verified", "mismatch"] | None = None
    name_dpr_kyc: Literal["match", "differs"] | None = None
    name_kyc_land: Literal["match", "differs"] | None = None
    location_dpr_land: Literal["match", "differs"] | None = None
    before_you_apply: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Top-level snapshot
# --------------------------------------------------------------------------


class CAMSnapshot(BaseModel):
    schema_version: str = CAM_SNAPSHOT_SCHEMA_VERSION
    report_id: str
    report_version: int
    analysis_run_id: str
    service_request_id: str

    borrower_profile: BorrowerProfile
    project_profile: ProjectProfile
    document_inventory: list[DocumentReference] = Field(default_factory=list)

    project_cost: ProjectCost
    means_of_finance: MeansOfFinance
    operational_assumptions: OperationalAssumptions

    financial_projections: list[FinancialProjection] = Field(default_factory=list)
    repayment_schedule: list[RepaymentPeriod] = Field(default_factory=list)
    financial_ratios: FinancialRatios
    scenario_analysis: list[ScenarioAnalysis] = Field(default_factory=list)

    findings: list[VerifiedFinding] = Field(default_factory=list)
    proposed_corrections: list[CorrectionProposal] = Field(default_factory=list)
    policy_assessment: PolicyAssessment

    readiness_assessment: ReadinessAssessment
    # None on reports made before this existed; the renderer then shows the
    # notice without a fee.
    document_verification: DocumentVerificationNotice | None = None
    # 2.x layout. The printed reference "PSR/<year>/<row number>", fixed when
    # the report is stored; None for 1.x reports and unsaved previews.
    report_reference: str | None = None
    financial_review: list[ReviewMeasure] = Field(default_factory=list)
    assumption_review: list[AssumptionRow] = Field(default_factory=list)
    document_checklist: list[DocumentStatus] = Field(default_factory=list)
    kyc_verification: KycVerification | None = None
    scheme_review: SchemeReview | None = None
    stress_test: StressTest | None = None
    re_review_by: datetime | None = None
    report_limitations: list[str] = Field(default_factory=list)
    evidence_registry: list[EvidenceReference] = Field(default_factory=list)

    generated_at: datetime
