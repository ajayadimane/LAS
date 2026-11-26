from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class SourcePage(BaseModel):
    page: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=35000)


class AnalysisInput(BaseModel):
    service_request_id: str = Field(min_length=1, max_length=128)
    wallet_document_id: str = Field(min_length=1, max_length=128)
    document_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    project_context: dict[str, str] = Field(default_factory=dict)
    pages: list[SourcePage] = Field(min_length=1, max_length=100)


# The categories an agent may use (enforced in gemini.AGENT_OUTPUT_SCHEMA).
# Aligned with cam_render._checks' status tiles (repayment / project cost /
# funding / identity) and finding_rules' document-defect set. Code-built
# findings additionally use "financial_calculation" and
# "interest_subvention". Finding.category itself stays a free string so
# reports stored before this vocabulary still load.
FINDING_CATEGORIES = (
    "project_cost",
    "funding",
    "repayment",
    "dscr",
    "revenue",
    "operating_costs",
    "operations",
    "market",
    "policy",
    "identity",
    "missing_document",
    "inconsistency",
    "assumption",
    "risk",
    "other",
)


class Finding(BaseModel):
    category: str
    severity: Literal["critical", "high", "medium", "low", "info"]
    title: str
    explanation: str
    evidence_page: int | None = None
    evidence_quote: str | None = None
    verification: Literal["source_verified", "unverified", "needs_clarification"] = (
        "unverified"
    )
    suggested_action: str
    # "calculated" = produced by financial_calc.py from the document's own
    # stated figures, never an LLM claim; default covers every pre-existing
    # LLM-authored finding, which is narrative judgment, not extraction.
    provenance: Literal[
        "extracted", "calculated", "ai_interpreted", "assumption", "unverified"
    ] = "ai_interpreted"

    # Populated by finding_consolidation.consolidate() after every agent has
    # run -- never by an agent itself. See that module for how each is
    # derived; all default so a pre-existing Finding (e.g. one already
    # persisted by report_store.py before this field existed) stays valid.
    dedup_key: str | None = None
    merged_duplicate_count: int = 1
    finding_class: Literal["document_defect", "business_risk"] | None = None
    priority: Literal["must_fix", "should_fix", "good_to_fix"] | None = None
    priority_rule: str | None = None

    # Set only by financial_calc.py/cam_engine_bridge.py for a calculated
    # finding that has a real DPR-stated figure and a real recomputed
    # figure to show side by side (e.g. "7.20 lakh" / "9.12 lakh"); never
    # set from narrative text. None means no such pair exists for this
    # finding -- never filled with a placeholder.
    dpr_stated_value: str | None = None
    recomputed_display_value: str | None = None
    # The action plan's "why it matters" and "should be" columns: fixed
    # text for code and checklist findings, a short model field otherwise.
    why_it_matters: str | None = None
    should_be: str | None = None

    # Stable id given in code before the verifier judges the candidate list
    # (finding_judge.assign_ids), so its verdicts can name a finding.
    finding_id: str | None = None
    # Set only on findings the verifier rejected (AnalysisResult.
    # rejected_findings), never on a finding shown to the customer.
    rejection_reason: str | None = None
    # The checklists.py item this finding answers; its title, category,
    # severity and priority come from that item, not from the model.
    check_id: str | None = None

    @field_validator("severity", mode="before")
    @classmethod
    def _normalize_severity_case(cls, v: object) -> object:
        """Gemini's structured-output enum constraint on this field is a
        request, not a guarantee (confirmed live: a real call returned
        "High"/"Medium" despite the schema asking for lowercase) -- a single
        role's casing choice must not fail the entire multi-role run."""
        return v.lower() if isinstance(v, str) else v


class DocumentFact(BaseModel):
    """Verbatim DPR metadata, not an authenticated Wallet identity."""
    field: Literal["borrower_name", "project_name", "business_sector", "business_subsector", "project_location", "project_purpose", "scheme_claimed", "bank_name"]
    value: str = Field(min_length=1, max_length=240)
    page: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=700)
    verified: bool = False


class ExternalSource(BaseModel):
    url: str
    domain: str
    tier: Literal["official", "secondary"]


class ExternalStatement(BaseModel):
    """One statement from a grounded web search (grounding.py), with the
    web sources Google attributed it to. Public information, never about
    the borrower."""

    kind: Literal["policy", "research"]
    text: str
    metric: str | None = None
    value_low: float | None = None
    value_high: float | None = None
    unit: str | None = None
    sources: list[ExternalSource] = Field(default_factory=list)
    official: bool = False
    retrieved_at: datetime | None = None


class Verdict(BaseModel):
    """The verifier's judgement on one candidate finding (by finding_id).
    Applied in code by finding_judge.apply_verdicts(), with guardrails."""

    finding_id: str
    decision: Literal["keep", "reject", "merge", "downgrade", "needs_clarification"]
    reason: str = ""
    # For "merge": the finding this one duplicates (the one that stays).
    duplicate_of: str | None = None
    # For "downgrade": the lower severity to apply.
    new_severity: Literal["critical", "high", "medium", "low", "info"] | None = None

    @field_validator("new_severity", mode="before")
    @classmethod
    def _normalize_severity_case(cls, v: object) -> object:
        return v.lower() if isinstance(v, str) else v


class ChecklistAnswer(BaseModel):
    """An agent's answer to one checklists.py item."""

    check_id: str
    status: Literal["issue", "ok", "not_applicable", "cannot_tell"]
    explanation: str = ""
    evidence_page: int | None = None
    evidence_quote: str | None = None


class AgentOutput(BaseModel):
    summary: str = ""
    document_facts: list[DocumentFact] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    # Only the verifier returns verdicts.
    verdicts: list[Verdict] = Field(default_factory=list)
    # Checklist answers (checklists.py); empty for roles without one.
    checks: list[ChecklistAnswer] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    service_request_id: str
    wallet_document_id: str
    document_sha256: str
    status: Literal["completed", "incomplete"]
    agent_outputs: dict[str, AgentOutput]
    findings: list[Finding]
    unresolved_questions: list[str]
    disclaimer: str = (
        "Pre-submission AI assistance only; not a bank sanction, eligibility "
        "certification, or authenticated source verification."
    )
    # Opaque dict (not a typed model here, to keep this base schema free of
    # the engine's dependencies) holding cam_engine_bridge.CamEngineOutputs,
    # set only when Settings.cam_engine_enabled is True (milestone 2b).
    # cam_adapter.py revalidates it into CamEngineOutputs when present.
    cam_engine_outputs: dict | None = None
    extracted_financials: dict | None = None
    # Structured record of the grouped financial extraction (which source
    # domains were attempted, failed, came back empty, were not found in the
    # document, or were recovered by a retry, plus the sections still
    # missing). The same facts also appear as FINANCIAL_* strings in
    # unresolved_questions, which cam_adapter's readiness logic reads; this
    # is the machine-readable form for audits and evaluation. None for runs
    # before it existed, and for the legacy single-extract client.
    extraction_audit: dict | None = None
    # Findings the verifier rejected, each with its rejection_reason: kept
    # for the audit trail, never shown as a customer finding.
    rejected_findings: list[Finding] = Field(default_factory=list)
    # Grounded public evidence (grounding.py): policy conditions and market
    # benchmarks with their web sources. Empty for older reports.
    external_evidence: list[ExternalStatement] = Field(default_factory=list)
    # The scheme rules the DPR's claim was checked against (schemes.py): one
    # record per claimed scheme, with the most it can give.
    scheme_checks: list[dict] = Field(default_factory=list)
    # Every code check of this run (financial_calc / unit_economics /
    # benchmarks CalculationResults as dicts): the readiness score and the
    # recomputed-figures page are built from them. Empty for older reports.
    calculations: list[dict] = Field(default_factory=list)
    # The customer's service details (bank name, project name) the analysis
    # ran with -- not borrower identity.
    service_details: dict[str, str] = Field(default_factory=dict)
    # The exact Gemini model string used for this run (e.g.
    # "gemini-2.5-flash"), read off the llm client at call time -- for
    # Annexure G's audit trail (Section 12). None for any result produced
    # before this field existed, or by a non-Gemini test double with no
    # `.model` attribute; never guessed from current config, since config
    # may have changed since a past run.
    gemini_model: str | None = None
    # {module: version} of the deterministic calculations this result used
    # (financial_calc.calculation_versions()), for Annexure G. None for a
    # result stored before 2026-11-25 -- never filled from current code.
    calculation_versions: dict[str, str] | None = None
