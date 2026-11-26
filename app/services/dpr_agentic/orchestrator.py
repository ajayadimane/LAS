"""LLM-led DPR workflow. This first increment is request-scoped, NOT a
durable job runner."""

import re
from dataclasses import asdict
from types import SimpleNamespace

from app.core.logging import get_logger

from .context_router import (
    financial_extraction_context,
    page_sections,
    select_context,
)
from .financial_calc import (
    calculation_versions,
    findings_from_calculations,
    run_all_checks,
)
from .finding_consolidation import consolidate
from .gemini import CURRENT_ROLE, GeminiAgentError
from .schemas import AgentOutput, AnalysisInput, AnalysisResult, Finding

logger = get_logger(__name__)

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
    "For each projected year, also extract the explicitly stated annual "
    "gross revenue (revenue) and total operating expenses (operating_expenses), "
    "in the DPR's lakh-rupee units if those are the document's own units. "
    "Do not confuse annual turnover with aggregate five-year turnover. "
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
        "Populate document_facts with borrower_name, project_name, business_sector, "
        "business_subsector, project_location, project_purpose, scheme_claimed, "
        "bank_name (the bank the DPR is addressed to) "
        "ONLY when explicitly present in the DPR. Each fact MUST carry an exact "
        "source page and a literal quote containing the claimed value. "
        "Do not infer identity from a phone number; do not invent bank eligibility. "
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
        "benchmarks. 'calculated_facts' holds code-computed operating "
        "checks (volume against capacity, year-1 utilisation, revenue "
        "against volume x price, price against variable cost, operating "
        "days); each already becomes its own finding when it fires, so do "
        "not raise those again or redo their arithmetic. Your job is what "
        "code cannot judge: whether the stated inputs themselves (capacity "
        "per day, operating days, price, per-unit cost, ramp-up) are "
        "realistic for this sector and location."
    ),
    "research": (
        "You are a market research analyst. No live search tool is "
        "available in this increment. Do not fabricate prices, "
        "quotations, websites, citations, market data or external "
        "verification. Identify research questions and evidence required, "
        "using only supplied material."
    ),
    "policy": (
        "You are a bank lending-readiness analyst for the lending bank "
        "named in the supplied context. No current authenticated bank "
        "policy corpus is attached in this increment. Never invent "
        "thresholds or claim bank approval. Identify documents "
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
        "You are the independent DPR findings verifier and the final judge of "
        "which findings reach the customer. 'candidate_findings' lists every "
        "finding raised so far, each with a finding_id. Return one verdict per "
        "candidate that needs action (a candidate you omit is kept):\n"
        "- merge: it describes the SAME underlying issue as another "
        "candidate, even if worded or categorised differently; set "
        "duplicate_of to the clearest one. Merge aggressively: the customer "
        "should see each real issue once.\n"
        "- reject: the source excerpts do not support it, it misreads the "
        "document, it contradicts 'calculated_facts', or it is generic advice "
        "not specific to this DPR.\n"
        "- downgrade: real but overstated; set new_severity lower. "
        "'critical' is only for issues that would plainly stop a bank from "
        "processing the loan.\n"
        "- needs_clarification: plausible but only the borrower can confirm.\n"
        "- keep: well supported as stated.\n"
        "Give a short reason for each verdict. Findings with provenance "
        "'calculated' are code-verified arithmetic: never reject or downgrade "
        "them. Findings with a check_id answer a fixed question and their "
        "title covers several cases (e.g. 'insurance or a back-up plan'): "
        "judge the explanation, not the title, and reject one only if the "
        "pages contradict its explanation; if they show part of what it "
        "says is missing, keep it. Add a new finding only for a material "
        "problem that NO "
        "candidate covers, with a verbatim quote and its page. Before adding "
        "one, compare it with every candidate, including the checklist "
        "findings (they have a check_id) and the calculated ones: if any "
        "candidate is about the same thing, even in different words or on a "
        "different page, do not add it -- merge or keep that candidate "
        "instead. Most reviews need no new finding, and never more than 3. "
        "Unresolved matters must remain questions."
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


# Roles whose failure (after GeminiAgentClient's own retries) must not cost
# the customer the whole report: the run continues with an explicit
# "unavailable" output for that role. Every other role (document, finance,
# risk, verifier) is required -- without it the report would be misleading,
# so its failure still fails the run.
OPTIONAL_ROLES = frozenset({"planner", "sector", "correction", "research", "policy"})


def role_unavailable_output(role: str) -> AgentOutput:
    return AgentOutput(
        summary=f"The {role} review could not be completed for this run.",
        unresolved_questions=[
            f"ROLE_UNAVAILABLE: {role} -- re-run the analysis to include it."
        ],
    )


def _unanswered(checklist: list[dict], output: AgentOutput) -> set[str]:
    """Checklist ids the agent gave no answer for."""
    return {c["id"] for c in checklist} - {a.check_id for a in output.checks}


async def generate_role(
    llm, role: str, context: dict, code_answers: dict | None = None
) -> AgentOutput:
    """One role's Gemini call, degrading an optional role's failure to
    role_unavailable_output() instead of raising. Callers still run
    verify_evidence() on the result. `code_answers` (checklist id ->
    ChecklistAnswer, from checklists.code_checklist_answers) are answered by
    code: those items are not asked, and their answers are used instead."""
    from .checklists import (
        CHECKLIST_INSTRUCTION,
        PLAIN_LANGUAGE_INSTRUCTION,
        apply_checklist,
        checklist_for,
        drop_code_settled,
    )

    prompt = PROMPTS[role] + PLAIN_LANGUAGE_INSTRUCTION
    code_answers = code_answers or {}
    checklist = [c for c in checklist_for(role) if c["id"] not in code_answers]
    if checklist:
        # Fixed questions, so every run covers the same topics with the
        # same titles and ratings (checklists.py).
        prompt += CHECKLIST_INSTRUCTION
        context = {**context, "checklist": checklist}
    token = CURRENT_ROLE.set(role)
    try:
        output = await llm.generate(prompt, context)
        if checklist and _unanswered(checklist, output):
            # Seen live (91364956ce58, 2026-11-26): the finance agent returned
            # a summary and one finding but no checklist answers, so nine
            # fixed questions vanished from that run's report. One retry.
            logger.warning(
                "dpr_checklist_unanswered role=%s missing=%d",
                role,
                len(_unanswered(checklist, output)),
            )
            retry = await llm.generate(prompt, context)
            if len(_unanswered(checklist, retry)) < len(
                _unanswered(checklist, output)
            ):
                output = retry
        if code_answers:
            # First, so apply_checklist keeps them over any agent answer.
            output = output.model_copy(
                update={"checks": [*code_answers.values(), *output.checks]}
            )
        return drop_code_settled(apply_checklist(role, output), code_answers)
    except GeminiAgentError:
        if role not in OPTIONAL_ROLES:
            raise
        logger.warning("dpr_role_unavailable role=%s", role)
        return role_unavailable_output(role)
    finally:
        CURRENT_ROLE.reset(token)


async def judge_role(llm, context: dict) -> AgentOutput:
    """The verifier's call. A client with judge() returns verdicts on the
    candidates; an older client or test double without it falls back to
    generate(), which yields no verdicts (every candidate is kept). The
    verifier is a required role: a failure fails the run."""
    from .checklists import PLAIN_LANGUAGE_INSTRUCTION

    prompt = PROMPTS["verifier"] + PLAIN_LANGUAGE_INSTRUCTION
    token = CURRENT_ROLE.set("verifier")
    try:
        if hasattr(llm, "judge"):
            return await llm.judge(prompt, context)
        return await llm.generate(prompt, context)
    finally:
        CURRENT_ROLE.reset(token)


def verify_evidence(result: AgentOutput, pages: dict[int, str]) -> AgentOutput:
    """Mark citation as source-verified only if its quote occurs on claimed page."""

    def normalized(value: str) -> str:
        return " ".join(re.findall(r"\w+", value.casefold()))

    for fact in result.document_facts:
        source = normalized(pages.get(fact.page, ""))
        quote = normalized(fact.quote)
        value = normalized(fact.value)
        # A substantive value must appear inside an actual same-page source
        # passage. Punctuation/spacing differences from LiteParse are ignored.
        fact.verified = bool(value and quote and quote in source and value in quote)
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
) -> tuple[list[dict], list[Finding], list[str], dict | None, dict | None, dict | None]:
    """Extract only source-grounded financial fields and independently check them.

    Production Gemini clients use smaller domain schemas. Existing injected
    LLM adapters retain the legacy single-extract interface for compatibility.
    Missing finance is reported as incomplete, never as a successful check.
    """
    from .financial_extraction import extract_financial_sections
    from .financial_schema import (
        aggregate_repayment_instalments,
        fill_net_profit_from_pbt,
        financial_extraction_completeness,
        merge_missing_financials,
        normalize_year_numbers,
    )

    pages = [SimpleNamespace(page=p["page"], text=p["text"]) for p in context["pages"]]
    questions: list[str] = []
    audit_record: dict | None = None
    if hasattr(llm, "extract_group"):
        financials, audit = await extract_financial_sections(
            llm, pages, context.get("project_context", {})
        )
        audit_record = {
            "attempted": list(audit.attempted),
            "failed": list(audit.failed),
            "empty": list(audit.empty),
            "absent_in_source": list(audit.absent_in_source),
            "recovered": list(audit.recovered),
        }
        if audit.failed:
            questions.append("FINANCIAL_EXTRACTION_FAILED: " + ", ".join(audit.failed))
        if audit.empty:
            questions.append(
                "FINANCIAL_EXTRACTION_EMPTY_RESPONSE: " + ", ".join(audit.empty)
            )
        if audit.absent_in_source:
            questions.append(
                "FINANCIAL_SOURCE_SECTION_NOT_IDENTIFIED: "
                + ", ".join(audit.absent_in_source)
            )
    else:
        # Test doubles and older custom Gemini clients continue to work.
        token = CURRENT_ROLE.set("financial_extraction")
        try:
            initial = financial_extraction_context(
                SimpleNamespace(
                    pages=pages, project_context=context.get("project_context", {})
                )
            )
            financials = await llm.extract(
                FINANCIAL_EXTRACTION_PROMPT, {"dpr": initial}
            )
        except GeminiAgentError:
            financials = None
        finally:
            CURRENT_ROLE.reset(token)
        if financials is None:
            return (
                [],
                [],
                ["FINANCIAL_EXTRACTION_INCOMPLETE: Extraction failed."],
                None,
                None,
                None,
            )
        completeness = financial_extraction_completeness(financials)
        if completeness["missing_sections"]:
            discovered = {name for p in pages for name in page_sections(p.text)}
            recover_sections = {
                "project_cost": {"project_cost", "vendor_quotation"},
                "means_of_finance": {"means_of_finance"},
                "repayment_schedule": {"repayment_schedule", "dscr"},
                "profit_and_loss": {"profit_and_loss", "cash_flow"},
            }
            selected = (
                set().union(
                    *(recover_sections[m] for m in completeness["missing_sections"])
                )
                & discovered
            )
            if selected:
                token = CURRENT_ROLE.set("financial_extraction_recovery")
                try:
                    specific = financial_extraction_context(
                        SimpleNamespace(
                            pages=pages,
                            project_context=context.get("project_context", {}),
                        ),
                        sections=selected,
                        page_limit=6,
                    )
                    additional = await llm.extract(
                        FINANCIAL_EXTRACTION_PROMPT
                        + " Extract only missing explicitly stated figures from these pages.",
                        {"dpr": specific},
                    )
                    financials = merge_missing_financials(financials, additional)
                except GeminiAgentError:
                    questions.append("FINANCIAL_EXTRACTION_FAILED: targeted recovery")
                finally:
                    CURRENT_ROLE.reset(token)

    # Both idempotent (the grouped path already applied them per domain):
    # calendar years -> 1..N, and per-instalment schedules -> yearly rows.
    # A no-tax year read as PBT only gets its net profit (DSCR needs it).
    financials = fill_net_profit_from_pbt(
        aggregate_repayment_instalments(normalize_year_numbers(financials))
    )
    completeness = financial_extraction_completeness(financials)
    if not completeness["has_financial_data"]:
        questions.append(
            "FINANCIAL_EXTRACTION_INCOMPLETE: No usable financial values were extracted. "
            "The CAM financial assessment has not been completed."
        )
    else:
        questions.extend(
            "FINANCIAL_SECTION_INCOMPLETE: " + section
            for section in completeness["missing_sections"]
        )

    logger.info(
        "dpr_financial_extraction_result missing_sections=%s has_financial_data=%s "
        "project_cost_components=%s means_of_finance_components=%s repayment_rows=%s "
        "projection_years=%s",
        completeness["missing_sections"],
        completeness["has_financial_data"],
        len(financials.cost_of_project_components),
        len(financials.means_of_finance_components),
        len(financials.repayment_table),
        len(financials.years),
    )
    calculations = run_all_checks(financials)
    findings = findings_from_calculations(calculations)
    engine_outputs = None
    acreage_checked = acreage_flagged = False
    if cam_engine_enabled and completeness["has_financial_data"]:
        from .cam_engine_bridge import run_harvester_engine

        engine, engine_findings = run_harvester_engine(financials)
        findings.extend(engine_findings)
        engine_outputs = engine.model_dump(mode="json")
        feasible = engine.feasible_acres_per_year
        acreage_checked = feasible is not None and feasible.status == "COMPUTED"
        acreage_flagged = any(
            "acres_per_year_feasibility" in (f.priority_rule or "")
            for f in engine_findings
        )
    # Sector-neutral operating checks (M3); always on, pure code.
    from .unit_economics import run_unit_economics

    ue_results, ue_findings = run_unit_economics(
        financials, acreage_checked=acreage_checked
    )
    calculations.extend(ue_results)
    findings.extend(ue_findings)
    # Benchmarks from the fixed table for the DPR's machine class; pure code.
    from .benchmarks import run_benchmark_checks

    bm_results, bm_findings, bm_record = run_benchmark_checks(
        financials, pages, acreage_flagged=acreage_flagged
    )
    calculations.extend(bm_results)
    findings.extend(bm_findings)
    financials.benchmark_used = bm_record
    if audit_record is not None:
        audit_record["missing_sections"] = list(completeness["missing_sections"])
    return (
        [asdict(x) for x in calculations],
        findings,
        questions,
        engine_outputs,
        financials.model_dump(mode="json"),
        audit_record,
    )


def no_usable_financial_extraction(raw: dict | None) -> bool:
    """Guard against 65k-token analysis on `{}` financial extraction."""
    from .financial_schema import ProjectFinancials, financial_extraction_completeness

    if not raw:
        return True
    return not financial_extraction_completeness(ProjectFinancials.model_validate(raw))[
        "has_financial_data"
    ]


async def partial_analysis_without_financials(
    payload: AnalysisInput,
    llm,
    questions: list[str],
    extraction_audit: dict | None = None,
) -> AnalysisResult:
    """Document/sector evidence is still useful; financial conclusions aren't.

    Without any usable numbers, running Finance/Risk/Verifier/Correction on
    model-authored prose is both expensive and misleading. Produce an explicitly
    incomplete borrower report, retaining independently supported non-financial
    source findings. Do not claim lending readiness or invent financial values.
    """
    logger.warning(
        "dpr_financial_early_stop no_usable_financial_data=true; "
        "running document/sector evidence review only"
    )
    pages = {p.page: p.text for p in payload.pages}
    outputs: dict[str, AgentOutput] = {}
    for role in ("document", "sector"):
        prior = {k: v.model_dump(mode="json") for k, v in outputs.items()}
        output = await generate_role(
            llm, role, select_context(role, payload, prior, [])
        )
        outputs[role] = verify_evidence(output, pages)
    findings = consolidate([f for out in outputs.values() for f in out.findings])
    extra = [q for out in outputs.values() for q in out.unresolved_questions]
    return AnalysisResult(
        service_request_id=payload.service_request_id,
        wallet_document_id=payload.wallet_document_id,
        document_sha256=payload.document_sha256,
        status="completed",  # pipeline finished; CAM readiness is INSUFFICIENT_INFORMATION
        agent_outputs=outputs,
        findings=findings,
        unresolved_questions=list(
            dict.fromkeys(
                list(questions)
                + [
                    "FINANCIAL_EXTRACTION_INCOMPLETE: Financial agents were not run "
                    "because no reliable numerical input was extracted."
                ]
                + extra
            )
        ),
        cam_engine_outputs=None,
        extracted_financials=None,
        extraction_audit=extraction_audit,
        gemini_model=getattr(llm, "model", None),
        calculation_versions=calculation_versions(),
    )


async def analyze(
    payload: AnalysisInput, llm, *, cam_engine_enabled: bool = False
) -> AnalysisResult:
    """The single analysis entry point for callers outside the worker (the
    internal /internal/dpr-agentic endpoint, scripts and tests).

    Delegates to parallel_graph.analyze_parallel(), the pipeline the worker
    runs, so every caller gets identical behaviour. This used to be a
    separate sequential loop over PROMPTS that diverged from the worker's
    graph (e.g. it made real LLM calls for the research/policy/report roles,
    which the graph deliberately does not).
    """
    from .parallel_graph import analyze_parallel  # avoid an import cycle

    return await analyze_parallel(payload, llm, cam_engine_enabled=cam_engine_enabled)
