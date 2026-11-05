"""LLM-led DPR workflow. This first increment is request-scoped, NOT a
durable job runner."""

from dataclasses import asdict

from .financial_calc import findings_from_calculations, run_all_checks
from .finding_consolidation import consolidate
from .gemini import CURRENT_ROLE, GeminiAgentError
from .schemas import AgentOutput, AnalysisInput, AnalysisResult, Finding

FINANCIAL_EXTRACTION_PROMPT = (
    "You are a financial data extraction specialist. Copy ONLY numeric figures "
    "explicitly written in the document into the given structure. Never compute, "
    "estimate, round, or infer a number that is not stated verbatim. When a "
    "document has two separate totals near each other (e.g. a 'Cost of Project' "
    "table and a 'Means of Finance' table on the same page), attribute each "
    "total only to the table it appears directly under -- never guess from "
    "position or proximity alone. If the document states its own DSCR for a "
    "year, or its own standard/subvention/effective interest rate (e.g. an AIF "
    "subvention), copy those figures verbatim too. If a year's P&L states a "
    "profit before tax and a tax figure separately from net profit, copy "
    "both into that year's profit_before_tax and tax_paid. Leave a field "
    "null if the document does not state it. No narrative, no commentary, "
    "no opinion -- figures only.\n\n"
    "For every field that is an {value, page, status} object: set 'page' to "
    "the page number the figure appears on, and 'status' to 'extracted' if "
    "you found a value or 'missing' if the document does not state it -- "
    "never set status to 'extracted' with value null, and never guess a page "
    "number for a value you did not find. This applies to: term_loan_amount, "
    "loan_tenor_years, loan_moratorium_years, fund_flow_sources_total, "
    "fund_flow_applications_total, repayment_table (one row per year of the "
    "document's own repayment schedule, each with principal_due and "
    "interest_due), and harvester_assumptions -- only populate "
    "harvester_assumptions if the project is a combine-harvester Custom "
    "Hiring Centre; leave it null for any other sector. Within "
    "harvester_assumptions, extract acres_per_year, acres_per_day, "
    "operating_days_per_season, seasons_per_year, price_per_acre (the "
    "rental rate charged per acre serviced), running_cost_per_acre_stated "
    "(the DPR's own headline running-cost-per-acre figure), "
    "running_cost_per_acre_corrected (a different running-cost-per-acre "
    "figure given elsewhere in the same document, e.g. in a detailed "
    "cost breakdown annexure, if one exists -- leave missing if the "
    "document gives only one such figure), fuel_litres_per_acre, "
    "fuel_price_per_litre, fuel_cost_stated (the P&L's own stated annual "
    "fuel cost), crew_wage_total_p_and_l_stated (the P&L's own stated "
    "annual crew wage figure), and crew_structure (one entry per staffing "
    "role named in the document, e.g. its Annexure II, with role, count "
    "and annual_wage_per_person, each with its own page and status)."
)

PROMPTS = {
    "document": (
        "You are the Document Agent. Normalize the supplied DPR facts and "
        "detect contradictions or omissions -- including the applicant's name, "
        "location or other identity details appearing inconsistently across "
        "pages (a common sign of a reused template from a different "
        "customer's document), and whether a vendor quotation or pro-forma "
        "invoice is attached with its total matching the stated project cost. "
        "Never invent missing values. Quote verbatim source text with its "
        "page number. Return concise evidence-backed findings, questions and "
        "summary."
    ),
    "planner": (
        "You are the DPR Planner. Determine the most material analyses "
        "needed for this Agri-MSME project. Identify the analytical agenda "
        "as unresolved questions; do not claim calculations were executed. "
        "Return structured JSON."
    ),
    "finance": (
        "You are a senior project-finance analyst. You are given "
        "independently recomputed figures under 'calculated_facts' (code, "
        "not an LLM) -- treat these as ground truth and build your "
        "narrative on them; never recompute or contradict a "
        "calculated_facts entry yourself. Check funding reconciliation, "
        "sales assumptions, costs, cashflows and consistency beyond what "
        "calculated_facts already covers, only where evidence permits. Flag "
        "a revenue projection that is stated as a bare figure with no shown "
        "derivation (e.g. no units x rate x working-days buildup) as a "
        "lower-confidence assumption, distinct from one the document "
        "derives explicitly. Do not assert numerical verification "
        "yourself; report uncertainty explicitly. Quote source text and "
        "page numbers. Never assert bank eligibility."
    ),
    "sector": (
        "You are an agricultural enterprise feasibility analyst. Evaluate "
        "technical capacity, utilization, seasonality, input-output "
        "conversion and business-specific operational assumptions. "
        "Separate evidence-backed inconsistencies from unverified "
        "benchmarks."
    ),
    "research": (
        "You are a market research analyst. No live search tool is "
        "available in this increment. Do not fabricate prices, "
        "quotations, websites, citations, market data or external "
        "verification. Identify research questions and evidence required, "
        "using only supplied material."
    ),
    "policy": (
        "You are an SBI lending-readiness analyst. No current "
        "authenticated SBI policy corpus is attached in this increment. "
        "Never invent thresholds or claim SBI approval. Identify documents "
        "or policy clauses needing verification, using only supplied "
        "context."
    ),
    "risk": (
        "You are a DPR risk reviewer. 'calculated_facts' are "
        "code-verified, not your own judgment -- correlate your findings "
        "with them rather than re-deriving numbers. Challenge "
        "interpretations and correlate findings from previous agents. "
        "Avoid duplicating issues already raised as a calculated_facts-"
        "based finding. Distinguish confirmed internal inconsistencies "
        "from assumptions requiring verification."
    ),
    "verifier": (
        "You are an independent DPR findings verifier. 'calculated_facts' "
        "entries are already code-verified; do not re-litigate their "
        "arithmetic, only whether prior agents' narrative conclusions "
        "follow from them. Challenge unsupported statements, circular "
        "reasoning, missing citations and arithmetic assertions made "
        "outside calculated_facts. Only report findings with source "
        "context; unresolved matters must remain questions."
    ),
    "correction": (
        "You are the DPR correction advisor. Propose corrections as "
        "suggested actions, not as actual edits. Never invent financial "
        "figures, alter original document facts or claim borrower consent."
    ),
    "report": (
        "You are the final DPR report writer. 'calculated_facts' are "
        "code-verified figures already included as findings; summarize "
        "them alongside the narrative findings, clearly as computed facts "
        "rather than AI opinion. Summarize evidenced issues, open "
        "questions and suggested pre-submission actions. Do not issue "
        "bank approval/rejection recommendations or a fabricated "
        "readiness score."
    ),
}


def verify_evidence(result: AgentOutput, pages: dict[int, str]) -> AgentOutput:
    """Mark citation as source-verified only if its quote occurs on claimed page."""
    for f in result.findings:
        if f.provenance == "calculated":
            # "calculated" must only ever be set by financial_calc.py's own
            # code; a narrative role's own finding must never be able to
            # claim code-verification for itself just by naming the label.
            f.provenance = "unverified"
        quote = f.evidence_quote
        if (
            f.evidence_page in pages
            and quote
            and " ".join(quote.split()).casefold()
            in " ".join(pages[f.evidence_page].split()).casefold()
        ):
            f.verification = "source_verified"
        else:
            f.verification = "unverified"
    return result


async def extract_calculated_facts(
    llm, context: dict, *, cam_engine_enabled: bool = False
) -> tuple[list[dict], list[Finding], list[str], dict | None]:
    """Independently recomputes financial figures from the document's own
    stated numbers (financial_calc.py; no LLM involved in the arithmetic
    itself). Degrades gracefully: an extraction failure never aborts the
    rest of the analysis, it just means no calculated_facts this run.

    `cam_engine_enabled` (Settings.cam_engine_enabled, default False --
    milestone 2b) additionally runs cam_engine_bridge.run_harvester_engine()
    over the same extracted financials, merging in any mismatch findings it
    finds and returning its structured outputs as the fourth element. False
    (the default) makes this call identical to before the engine existed.
    """
    role_token = CURRENT_ROLE.set("financial_extraction")
    try:
        financials = await llm.extract(FINANCIAL_EXTRACTION_PROMPT, {"dpr": context})
    except GeminiAgentError:
        return (
            [],
            [],
            [
                "Automated financial figure extraction failed; verify costs, "
                "means of finance and DSCR manually."
            ],
            None,
        )
    finally:
        CURRENT_ROLE.reset(role_token)
    results = run_all_checks(financials)
    findings = findings_from_calculations(results)
    engine_outputs_dict = None
    if cam_engine_enabled:
        from .cam_engine_bridge import run_harvester_engine

        engine_outputs, engine_findings = run_harvester_engine(financials)
        findings = findings + engine_findings
        engine_outputs_dict = engine_outputs.model_dump(mode="json")
    return [asdict(r) for r in results], findings, [], engine_outputs_dict


async def analyze(
    payload: AnalysisInput, llm, *, cam_engine_enabled: bool = False
) -> AnalysisResult:
    pages = {page.page: page.text for page in payload.pages}
    if len(pages) != len(payload.pages):
        raise ValueError("Duplicate page numbers are not allowed")
    # Max token-size bound: reject truncation rather than silently drop
    # vital appendices.
    if sum(len(s) for s in pages.values()) > 180_000:
        raise ValueError(
            "Document exceeds analysis context limit; must be segmented "
            "with a retrieval adapter"
        )
    context = {
        "project_context": payload.project_context,
        "pages": [{"page": k, "text": v} for k, v in pages.items()],
    }

    (
        calculated_facts,
        calculated_findings,
        extraction_questions,
        cam_engine_outputs,
    ) = await extract_calculated_facts(
        llm, context, cam_engine_enabled=cam_engine_enabled
    )

    outputs: dict[str, AgentOutput] = {}
    for name in PROMPTS:
        # All agents use Gemini with separate roles, and previous
        # structured outputs.
        prior = {k: v.model_dump(mode="json") for k, v in outputs.items()}
        output = await llm.generate(
            PROMPTS[name],
            {
                "dpr": context,
                "previous_agent_outputs": prior,
                "calculated_facts": calculated_facts,
            },
        )
        outputs[name] = verify_evidence(output, pages)
    # Preserve all findings with provenance; do not downgrade unknown
    # evidence to verified.
    findings: list[Finding] = list(calculated_findings)
    questions: list[str] = list(extraction_questions)
    for output in outputs.values():
        findings.extend(output.findings)
        questions.extend(output.unresolved_questions)
    findings = consolidate(findings)
    return AnalysisResult(
        service_request_id=payload.service_request_id,
        wallet_document_id=payload.wallet_document_id,
        document_sha256=payload.document_sha256,
        status="completed",
        agent_outputs=outputs,
        findings=findings,
        unresolved_questions=list(dict.fromkeys(questions)),
        cam_engine_outputs=cam_engine_outputs,
        gemini_model=getattr(llm, "model", None),
    )
