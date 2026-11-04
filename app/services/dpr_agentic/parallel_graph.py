"""Dependency-aware LangGraph workflow; independent analysts execute in one
parallel superstep.

This graph deliberately does not interact with Wallet or decide report
release.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from .finding_consolidation import consolidate
from .gemini import CURRENT_ROLE
from .orchestrator import PROMPTS, extract_calculated_facts, verify_evidence
from .schemas import AgentOutput, AnalysisInput, AnalysisResult, Finding


class GraphState(TypedDict, total=False):
    payload: dict
    outputs: Annotated[dict[str, dict], operator.or_]
    errors: Annotated[list[str], operator.add]
    # Set once before the graph runs, read-only by every node -- no reducer
    # needed since no node ever returns this key.
    calculated_facts: list[dict]


def build_graph(llm, *, checkpointer=None):
    """Create a graph for a single run; caller supplies the bounded Gemini client."""
    from langgraph.graph import END, START, StateGraph

    async def invoke(name: str, state: GraphState) -> GraphState:
        inp = AnalysisInput.model_validate(state["payload"])
        pages = {p.page: p.text for p in inp.pages}
        if len(pages) != len(inp.pages):
            raise ValueError("Duplicate page numbers")
        context = {
            "project_context": inp.project_context,
            "pages": [p.model_dump() for p in inp.pages],
        }
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
        role_token = CURRENT_ROLE.set(name)
        try:
            output = await llm.generate(
                PROMPTS[name],
                {
                    "dpr": context,
                    "previous_agent_outputs": prior,
                    "calculated_facts": state.get("calculated_facts", []),
                },
            )
        finally:
            CURRENT_ROLE.reset(role_token)
        verified = verify_evidence(output, pages)
        return {"outputs": {name: verified.model_dump(mode="json")}}

    graph = StateGraph(GraphState)
    for name in PROMPTS:

        async def node(state, role=name):
            return await invoke(role, state)

        graph.add_node(name, node)
    graph.add_edge(START, "document")
    graph.add_edge("document", "planner")
    for role in ("finance", "sector", "research", "policy"):
        graph.add_edge("planner", role)
        graph.add_edge(role, "risk")
    graph.add_edge("risk", "verifier")
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
    ) = await extract_calculated_facts(
        llm, context, cam_engine_enabled=cam_engine_enabled
    )

    graph = build_graph(llm, checkpointer=checkpointer)
    result = await graph.ainvoke(
        {
            "payload": payload.model_dump(mode="json"),
            "outputs": {},
            "errors": [],
            "calculated_facts": calculated_facts,
        },
        config={
            "configurable": {"thread_id": thread_id or payload.document_sha256},
            "recursion_limit": 20,
        },
    )
    outputs = {k: AgentOutput.model_validate(v) for k, v in result["outputs"].items()}
    findings: list[Finding] = list(calculated_findings)
    findings.extend(f for out in outputs.values() for f in out.findings)
    findings = consolidate(findings)
    questions = list(extraction_questions)
    questions.extend(q for out in outputs.values() for q in out.unresolved_questions)
    return AnalysisResult(
        service_request_id=payload.service_request_id,
        wallet_document_id=payload.wallet_document_id,
        document_sha256=payload.document_sha256,
        status="completed",
        agent_outputs=outputs,
        findings=findings,
        unresolved_questions=list(dict.fromkeys(questions)),
        cam_engine_outputs=cam_engine_outputs,
    )
