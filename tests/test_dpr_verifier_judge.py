"""The verifier as judge: verdicts on consolidated candidates, applied in
code with guardrails (finding_judge.py), wired into the graph."""

import asyncio
import hashlib

from app.services.dpr_agentic.financial_schema import ProjectFinancials
from app.services.dpr_agentic.finding_judge import apply_verdicts, assign_ids
from app.services.dpr_agentic.orchestrator import PROMPTS, analyze
from app.services.dpr_agentic.schemas import (
    AgentOutput,
    AnalysisInput,
    Finding,
    Verdict,
)


def _f(title, *, severity="high", provenance="ai_interpreted", quote=None):
    return Finding(
        category="operations",
        severity=severity,
        title=title,
        explanation=f"{title}.",
        evidence_page=3,
        evidence_quote=quote,
        suggested_action="Fix it.",
        provenance=provenance,
    )


def _ids(findings):
    return [f.finding_id for f in findings]


def test_ids_are_stable_and_inputs_untouched():
    raw = [_f("A"), _f("B")]
    assert _ids(assign_ids(raw)) == ["F01", "F02"]
    assert raw[0].finding_id is None


def test_merge_folds_a_duplicate_into_its_target():
    c = assign_ids(
        [
            _f("215 days unrealistic", severity="critical", quote="215 days"),
            _f("Operating window too long", quote="working days"),
        ]
    )
    kept, rejected = apply_verdicts(
        c, [Verdict(finding_id="F02", decision="merge", duplicate_of="F01")]
    )
    assert _ids(kept) == ["F01"] and rejected == []
    assert kept[0].merged_duplicate_count == 2
    assert '"working days"' in kept[0].explanation


def test_reject_moves_to_rejected_with_reason():
    c = assign_ids([_f("A"), _f("Generic advice")])
    kept, rejected = apply_verdicts(
        c, [Verdict(finding_id="F02", decision="reject", reason="Not specific")]
    )
    assert _ids(kept) == ["F01"]
    assert rejected[0].rejection_reason == "Not specific"


def test_calculated_findings_cannot_be_rejected_or_downgraded():
    c = assign_ids([_f("DSCR 0.8", provenance="calculated")])
    kept, rejected = apply_verdicts(
        c,
        [
            Verdict(finding_id="F01", decision="reject", reason="x"),
            Verdict(finding_id="F01", decision="downgrade", new_severity="info"),
        ],
    )
    assert rejected == [] and kept[0].severity == "high"


def test_merging_a_calculated_finding_keeps_the_calculated_one():
    c = assign_ids([_f("Acreage", provenance="calculated"), _f("Acreage narrative")])
    kept, _ = apply_verdicts(
        c, [Verdict(finding_id="F01", decision="merge", duplicate_of="F02")]
    )
    assert _ids(kept) == ["F01"]
    assert kept[0].provenance == "calculated" and kept[0].merged_duplicate_count == 2


def test_two_calculated_findings_are_never_merged():
    c = assign_ids(
        [_f("Fuel", provenance="calculated"), _f("Crew", provenance="calculated")]
    )
    kept, _ = apply_verdicts(
        c, [Verdict(finding_id="F02", decision="merge", duplicate_of="F01")]
    )
    assert _ids(kept) == ["F01", "F02"]


def test_downgrade_only_lowers_and_recomputes_priority():
    c = assign_ids([_f("A", severity="critical"), _f("B", severity="low")])
    kept, _ = apply_verdicts(
        c,
        [
            Verdict(finding_id="F01", decision="downgrade", new_severity="medium"),
            Verdict(
                finding_id="F02", decision="downgrade", new_severity="high"
            ),  # a raise
        ],
    )
    assert [f.severity for f in kept] == ["medium", "low"]
    assert kept[0].priority == "good_to_fix"


def test_bad_verdicts_are_ignored_and_silence_keeps():
    c = assign_ids([_f("A"), _f("B")])
    kept, rejected = apply_verdicts(
        c,
        [
            Verdict(finding_id="F99", decision="reject"),
            Verdict(finding_id="F01", decision="merge", duplicate_of="F01"),
            Verdict(finding_id="F01", decision="merge", duplicate_of="F77"),
        ],
    )
    assert _ids(kept) == ["F01", "F02"] and rejected == []


def test_needs_clarification_sets_verification():
    c = assign_ids([_f("A")])
    kept, _ = apply_verdicts(
        c, [Verdict(finding_id="F01", decision="needs_clarification")]
    )
    assert kept[0].verification == "needs_clarification"


# --- wired into the graph -------------------------------------------------

PAYLOAD = AnalysisInput(
    service_request_id="SR-1",
    wallet_document_id="W-1",
    document_sha256=hashlib.sha256(b"judge").hexdigest(),
    pages=[{"page": 3, "text": "The harvester will operate 215 working days a year."}],
)


class JudgingLLM:
    def __init__(self):
        self.judge_context = None

    async def generate(self, prompt, context):
        role = next(k for k, v in PROMPTS.items() if prompt.startswith(v))
        titles = {
            "finance": ["Unrealistic 215 working days", "Generic: keep good records"],
            "sector": ["Operating window of 215 days is implausible"],
            "risk": ["Debt coverage masked by 215 operating days"],
            "correction": ["Correction restates the 215-day issue"],
        }.get(role, [])
        return AgentOutput(
            summary=role,
            findings=[_f(t, quote="215 working days") for t in titles],
            unresolved_questions=[f"{role} agenda question"],
        )

    async def judge(self, prompt, context):
        self.judge_context = context
        ids = {c["title"]: c["finding_id"] for c in context["candidate_findings"]}
        keep = ids["Unrealistic 215 working days"]
        return AgentOutput(
            summary="judged",
            verdicts=[
                Verdict(
                    finding_id=ids["Operating window of 215 days is implausible"],
                    decision="merge",
                    duplicate_of=keep,
                ),
                Verdict(
                    finding_id=ids["Debt coverage masked by 215 operating days"],
                    decision="merge",
                    duplicate_of=keep,
                ),
                Verdict(
                    finding_id=ids["Generic: keep good records"],
                    decision="reject",
                    reason="Generic advice",
                ),
            ],
        )

    async def extract(self, prompt, context):
        return ProjectFinancials(cost_of_project_stated_total=30.0)


def test_graph_shows_each_issue_once_and_keeps_rejections_on_record():
    llm = JudgingLLM()
    result = asyncio.run(analyze(PAYLOAD, llm))
    titles = [f.title for f in result.findings]
    assert titles == ["Unrealistic 215 working days"]
    assert result.findings[0].merged_duplicate_count == 3
    assert [f.title for f in result.rejected_findings] == ["Generic: keep good records"]
    # Correction's restatement never bypasses the judge.
    assert "Correction restates the 215-day issue" not in titles
    # The judge saw ids, and the planner's agenda is not a customer question.
    assert all(c["finding_id"] for c in llm.judge_context["candidate_findings"])
    assert "planner agenda question" not in result.unresolved_questions
    assert "finance agenda question" in result.unresolved_questions


def test_gemini_judge_sends_the_verifier_schema_and_parses_verdicts():
    import json

    import httpx

    from app.services.dpr_agentic.gemini import GeminiAgentClient
    from app.services.dpr_agentic.schemas import FINDING_CATEGORIES

    sent = []
    body = {
        "summary": "s",
        "findings": [],
        "unresolved_questions": [],
        "verdicts": [
            {
                "finding_id": "F02",
                "decision": "merge",
                "reason": "dup",
                "duplicate_of": "F01",
                "new_severity": None,
            }
        ],
    }

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {"parts": [{"text": json.dumps(body)}]},
                    }
                ]
            },
        )

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await GeminiAgentClient(http, "fake-key", "gemini-3.8-flash").judge(
                "Prompt", {"candidate_findings": []}
            )

    out = asyncio.run(go())
    assert out.verdicts[0].duplicate_of == "F01"
    schema = sent[0]["generationConfig"]["responseSchema"]
    assert "verdicts" in schema["required"]
    category = schema["properties"]["findings"]["items"]["properties"]["category"]
    assert category["enum"] == list(FINDING_CATEGORIES)


def test_verifier_restating_a_kept_finding_is_folded_in():
    from app.services.dpr_agentic.finding_judge import absorb_restatements
    from app.services.dpr_agentic.schemas import Finding

    def f(title, page=7):
        return Finding(
            category="operating_costs",
            severity="high",
            title=title,
            explanation="x",
            evidence_page=page,
            suggested_action="Fix.",
        )

    kept = [
        f(
            "Total Absence of Working Capital Allocation Despite Substantial "
            "Operational Expenses"
        )
    ]
    new = [
        f("Zero Working Capital Provisioning Despite Substantial Operational Expenses"),
        f("Insurance premium not budgeted"),
    ]
    out = absorb_restatements(kept, new)
    assert [x.title for x in out] == [kept[0].title, "Insurance premium not budgeted"]
    assert out[0].merged_duplicate_count == 2
    assert kept[0].merged_duplicate_count == 1  # input untouched
