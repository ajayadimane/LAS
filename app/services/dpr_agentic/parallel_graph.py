"""Dependency-aware LangGraph workflow; independent analysts execute in one
parallel superstep.

This graph deliberately does not interact with Wallet or decide report
release.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from .context_router import select_context, verifier_judge_context
from .financial_calc import calculation_versions
from .finding_consolidation import consolidate
from .finding_judge import absorb_restatements, apply_verdicts, assign_ids
from .orchestrator import (
    PROMPTS,
    extract_calculated_facts,
    generate_role,
    judge_role,
    no_usable_financial_extraction,
    partial_analysis_without_financials,
    verify_evidence,
)
from .schemas import AgentOutput, AnalysisInput, AnalysisResult, Finding


class GraphState(TypedDict, total=False):
    payload: dict
    outputs: Annotated[dict[str, dict], operator.or_]
    errors: Annotated[list[str], operator.add]
    # Set once before the graph runs, read-only by every node -- no reducer
    # needed since no node ever returns this key.
    calculated_facts: list[dict]
    # Code-built findings (financial_calc / cam_engine_bridge), set once.
    calculated_findings: list[dict]
    # Written once each, by the "candidates" and "verifier" nodes.
    candidates: list[dict]
    judged: list[dict]
    rejected: list[dict]
    # Extracted financials (set once) and grounded public evidence written
    # by the research and policy nodes (grounding.py).
    financials: dict | None
    external: Annotated[list[dict], operator.add]
    # Written once, by the "candidates" node (schemes.py records).
    scheme_checks: list[dict]


# Roles whose findings are candidates for the verifier's judgement. The
# correction role runs after judging and only proposes fixes; its findings
# are not added to the customer's list (they would bypass the judge).
CANDIDATE_ROLES = ("document", "finance", "sector", "research", "policy", "risk")
# Roles whose unresolved_questions reach the customer. The planner's are its
# own analysis agenda, not questions for the borrower.
QUESTION_ROLES = (
    "document",
    "finance",
    "sector",
    "research",
    "policy",
    "risk",
    "verifier",
    "correction",
)


def build_graph(
    llm, *, checkpointer=None, grounding_enabled=False, grounding_cache=None
):
    """Create a graph for a single run; caller supplies the bounded Gemini client.
    `grounding_enabled`: research and policy search the web (grounding.py);
    off, they return the fixed "not verified" output."""
    from langgraph.graph import END, START, StateGraph

    async def invoke(name: str, state: GraphState) -> GraphState:
        inp = AnalysisInput.model_validate(state["payload"])
        pages = {p.page: p.text for p in inp.pages}
        if len(pages) != len(inp.pages):
            raise ValueError("Duplicate page numbers")
        # Restrict prior context to completed dependencies rather than
        # stuffing all agent outputs.
        dependencies = {
            "planner": ["document"],
            "finance": ["document", "planner"],
            "sector": ["document", "planner"],
            "research": ["document", "planner"],
            "policy": ["document", "planner"],
            "risk": ["finance", "sector", "research", "policy"],
            "verifier": ["risk", "finance", "sector", "research", "policy"],
            "correction": ["verifier", "risk"],
            "report": ["correction", "verifier", "risk"],
        }
        prior = {
            key: state.get("outputs", {})[key]
            for key in dependencies.get(name, [])
            if key in state.get("outputs", {})
        }
        # A client without grounded_search (older clients, test doubles)
        # runs without grounding, like judge_role's fallback.
        if (
            name in ("research", "policy")
            and grounding_enabled
            and hasattr(llm, "grounded_search")
        ):
            return await grounded(name, state, prior)
        if name in ("research", "policy"):
            # Grounding off: no live search, so nothing external is claimed.
            return {
                "outputs": {
                    name: AgentOutput(
                        summary=(
                            "External market/policy evidence unavailable; "
                            "no eligibility verified."
                        ),
                        unresolved_questions=[
                            "Verify current market references and the lending "
                            "bank's product requirements with authoritative "
                            "sources."
                        ],
                    ).model_dump(mode="json")
                }
            }
        if name == "report":
            # Report rendering uses the structured CAMSnapshot and does not
            # need a separate LLM pass to restate the same findings.
            return {
                "outputs": {
                    name: AgentOutput(
                        summary=prior.get("risk", {}).get(
                            "summary",
                            "Analysis complete; see verified findings and "
                            "remaining questions.",
                        ),
                    ).model_dump(mode="json")
                }
            }
        if name == "verifier":
            return await judge(state, inp, pages)
        if name == "correction" and "verifier" in prior:
            # Correction works from the judged list, not the verifier's own
            # (new-findings-only) output.
            prior = {
                **prior,
                "verifier": {**prior["verifier"], "findings": state.get("judged", [])},
            }
        from .checklists import code_checklist_answers
        from .financial_schema import ProjectFinancials

        output = await generate_role(
            llm,
            name,
            select_context(name, inp, prior, state.get("calculated_facts", [])),
            code_checklist_answers(
                name,
                ProjectFinancials.model_validate(state["financials"])
                if state.get("financials")
                else None,
            ),
        )
        verified = verify_evidence(output, pages)
        return {"outputs": {name: verified.model_dump(mode="json")}}

    async def grounded(name: str, state: GraphState, prior: dict) -> GraphState:
        from .financial_schema import ProjectFinancials
        from .gemini import GeminiAgentError
        from .grounding import (
            benchmark_findings,
            build_search_brief,
            role_output,
            run_grounded_role,
        )
        from .orchestrator import role_unavailable_output
        from .schemas import DocumentFact

        inp = AnalysisInput.model_validate(state["payload"])
        facts = [
            DocumentFact.model_validate(f)
            for f in prior.get("document", {}).get("document_facts", [])
        ]
        fin = (
            ProjectFinancials.model_validate(state["financials"])
            if state.get("financials")
            else None
        )
        brief = build_search_brief(facts, fin, inp.project_context)
        try:
            statements = await run_grounded_role(llm, name, brief, grounding_cache)
        except GeminiAgentError:
            # Optional role: the report still ships, saying so.
            return {
                "outputs": {name: role_unavailable_output(name).model_dump(mode="json")}
            }
        output = role_output(name, statements, brief)
        if name == "research":
            output.findings = benchmark_findings(statements, fin)
        return {
            "outputs": {name: output.model_dump(mode="json")},
            "external": [s.model_dump(mode="json") for s in statements],
        }

    async def gather_candidates(state: GraphState) -> GraphState:
        """Code: every analyst finding plus the calculated ones,
        deduplicated by rule, then given stable ids for the verifier."""
        findings = [
            Finding.model_validate(f) for f in state.get("calculated_findings", [])
        ]
        for role in CANDIDATE_ROLES:
            if role in state.get("outputs", {}):
                findings.extend(
                    AgentOutput.model_validate(state["outputs"][role]).findings
                )
        # Scheme fit (schemes.py): needs the document agent's claimed scheme
        # and the extracted financials, both available only here.
        from .financial_schema import ProjectFinancials
        from .schemas import DocumentFact
        from .schemes import run_scheme_checks

        facts = [
            DocumentFact.model_validate(f)
            for f in state.get("outputs", {})
            .get("document", {})
            .get("document_facts", [])
        ]
        fin = (
            ProjectFinancials.model_validate(state["financials"])
            if state.get("financials")
            else None
        )
        scheme_findings, scheme_records = run_scheme_checks(facts, fin)
        findings.extend(scheme_findings)
        candidates = assign_ids(consolidate(findings))
        return {
            "candidates": [c.model_dump(mode="json") for c in candidates],
            "scheme_checks": scheme_records,
        }

    async def judge(state: GraphState, inp, pages) -> GraphState:
        candidates = [Finding.model_validate(c) for c in state.get("candidates", [])]
        output = await judge_role(
            llm,
            verifier_judge_context(
                inp, state.get("candidates", []), state.get("calculated_facts", [])
            ),
        )
        output = verify_evidence(output, pages)
        kept, rejected = apply_verdicts(candidates, output.verdicts)
        # The verifier's own new findings join the kept ones; consolidate
        # again in case one restates a kept finding.
        judged = consolidate(absorb_restatements(kept, list(output.findings)))
        return {
            "outputs": {"verifier": output.model_dump(mode="json")},
            "judged": [f.model_dump(mode="json") for f in judged],
            "rejected": [f.model_dump(mode="json") for f in rejected],
        }

    graph = StateGraph(GraphState)
    for name in PROMPTS:

        async def node(state, role=name):
            return await invoke(role, state)

        graph.add_node(name, node)
    graph.add_node("candidates", gather_candidates)
    graph.add_edge(START, "document")
    graph.add_edge("document", "planner")
    for role in ("finance", "sector", "research", "policy"):
        graph.add_edge("planner", role)
        graph.add_edge(role, "risk")
    graph.add_edge("risk", "candidates")
    graph.add_edge("candidates", "verifier")
    graph.add_edge("verifier", "correction")
    graph.add_edge("correction", "report")
    graph.add_edge("report", END)
    return graph.compile(checkpointer=checkpointer)


async def analyze_parallel(
    payload: AnalysisInput,
    llm,
    *,
    checkpointer=None,
    thread_id: str | None = None,
    cam_engine_enabled: bool = False,
    grounding_enabled: bool = False,
    grounding_cache=None,
) -> AnalysisResult:
    pages = {p.page: p.text for p in payload.pages}
    if len(pages) != len(payload.pages):
        raise ValueError("Duplicate page numbers")
    if sum(len(s) for s in pages.values()) > 180_000:
        raise ValueError("Document context limit exceeded; segment before analysis")

    context = {
        "project_context": payload.project_context,
        "pages": [p.model_dump() for p in payload.pages],
    }
    (
        calculated_facts,
        calculated_findings,
        extraction_questions,
        cam_engine_outputs,
        extracted_financials,
        extraction_audit,
    ) = await extract_calculated_facts(
        llm, context, cam_engine_enabled=cam_engine_enabled
    )

    if no_usable_financial_extraction(extracted_financials):
        return await partial_analysis_without_financials(
            payload, llm, extraction_questions, extraction_audit
        )

    graph = build_graph(
        llm,
        checkpointer=checkpointer,
        grounding_enabled=grounding_enabled,
        grounding_cache=grounding_cache,
    )
    result = await graph.ainvoke(
        {
            "payload": payload.model_dump(mode="json"),
            "outputs": {},
            "errors": [],
            "calculated_facts": calculated_facts,
            "calculated_findings": [
                f.model_dump(mode="json") for f in calculated_findings
            ],
            "financials": extracted_financials,
            "external": [],
        },
        config={
            "configurable": {"thread_id": thread_id or payload.document_sha256},
            "recursion_limit": 20,
        },
    )
    outputs = {k: AgentOutput.model_validate(v) for k, v in result["outputs"].items()}
    # The verifier's judged list is the customer's finding list.
    findings = consolidate(
        [Finding.model_validate(f) for f in result.get("judged", [])]
    )
    rejected = [Finding.model_validate(f) for f in result.get("rejected", [])]
    questions = list(extraction_questions)
    questions.extend(
        q
        for role, out in outputs.items()
        for q in out.unresolved_questions
        # Any role's "unavailable" notice is a run-status fact.
        if role in QUESTION_ROLES or q.startswith("ROLE_UNAVAILABLE:")
    )
    from .grounding import lending_bank_conflict

    document = outputs.get("document")
    if mismatch := lending_bank_conflict(
        document.document_facts if document else [], payload.project_context
    ):
        questions.append(mismatch)
    return AnalysisResult(
        service_request_id=payload.service_request_id,
        wallet_document_id=payload.wallet_document_id,
        document_sha256=payload.document_sha256,
        status="completed",
        agent_outputs=outputs,
        findings=findings,
        rejected_findings=rejected,
        unresolved_questions=list(dict.fromkeys(questions)),
        cam_engine_outputs=cam_engine_outputs,
        extracted_financials=extracted_financials,
        extraction_audit=extraction_audit,
        external_evidence=result.get("external", []),
        scheme_checks=result.get("scheme_checks", []),
        calculations=calculated_facts,
        service_details={
            k: v
            for k, v in payload.project_context.items()
            if k in ("bank_name", "project_name")
        },
        gemini_model=getattr(llm, "model", None),
        calculation_versions=calculation_versions(),
    )
