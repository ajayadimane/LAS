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

    @field_validator("severity", mode="before")
    @classmethod
    def _normalize_severity_case(cls, v: object) -> object:
        """Gemini's structured-output enum constraint on this field is a
        request, not a guarantee (confirmed live: a real call returned
        "High"/"Medium" despite the schema asking for lowercase) -- a single
        role's casing choice must not fail the entire multi-role run."""
        return v.lower() if isinstance(v, str) else v


class AgentOutput(BaseModel):
    summary: str = ""
    findings: list[Finding] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)


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
    # The exact Gemini model string used for this run (e.g.
    # "gemini-2.5-flash"), read off the llm client at call time -- for
    # Annexure G's audit trail (Section 12). None for any result produced
    # before this field existed, or by a non-Gemini test double with no
    # `.model` attribute; never guessed from current config, since config
    # may have changed since a past run.
    gemini_model: str | None = None
