"""Fixed per-role checklists (checklists.py): same questions every run;
code sets each finding's title, category, severity and priority."""

import pytest

from app.services.dpr_agentic.checklists import (
    CHECKLIST_INSTRUCTION,
    CHECKLISTS,
    MAX_EXTRA_FINDINGS,
    apply_checklist,
)
from app.services.dpr_agentic.evaluation import stability
from app.services.dpr_agentic.finding_consolidation import consolidate
from app.services.dpr_agentic.finding_judge import apply_verdicts, assign_ids
from app.services.dpr_agentic.orchestrator import PROMPTS, generate_role
from app.services.dpr_agentic.schemas import (
    FINDING_CATEGORIES,
    AgentOutput,
    ChecklistAnswer,
    Finding,
    Verdict,
)


def _free(title, severity="medium"):
    return Finding(
        category="other",
        severity=severity,
        title=title,
        explanation="x",
        suggested_action="y",
    )


def test_items_are_well_formed_and_ids_unique():
    items = [i for role in CHECKLISTS.values() for i in role]
    assert len({i.id for i in items}) == len(items)
    for i in items:
        assert i.category in FINDING_CATEGORIES
        assert i.severity in ("critical", "high", "medium", "low", "info")
        assert i.priority in ("must_fix", "should_fix", "good_to_fix")


def test_issue_answers_become_findings_rated_by_the_item_not_the_model():
    output = AgentOutput(
        checks=[
            ChecklistAnswer(
                check_id="RISK_SINGLE_ASSET",
                status="issue",
                explanation="One harvester, no insurance.",
                evidence_page=7,
                evidence_quote="one combine harvester",
            ),
            ChecklistAnswer(check_id="RISK_SENSITIVITY", status="ok"),
            ChecklistAnswer(check_id="RISK_PROMOTER_EXPERIENCE", status="cannot_tell"),
            ChecklistAnswer(check_id="NOT_AN_ITEM", status="issue"),
            ChecklistAnswer(
                check_id="RISK_SINGLE_ASSET", status="issue", explanation="repeated"
            ),
        ]
    )
    [finding] = apply_checklist("risk", output).findings
    item = next(i for i in CHECKLISTS["risk"] if i.id == "RISK_SINGLE_ASSET")
    assert (finding.title, finding.category, finding.severity, finding.priority) == (
        item.title,
        item.category,
        item.severity,
        item.priority,
    )
    assert finding.check_id == "RISK_SINGLE_ASSET"
    assert finding.explanation == "One harvester, no insurance."
    assert finding.evidence_page == 7


def test_free_form_findings_are_capped_keeping_the_most_severe():
    extras = [_free(f"low {i}", "low") for i in range(4)] + [_free("big", "high")]
    out = apply_checklist("sector", AgentOutput(findings=extras))
    assert len(out.findings) == MAX_EXTRA_FINDINGS
    assert out.findings[0].title == "big"


def test_roles_without_a_checklist_are_untouched():
    output = AgentOutput(findings=[_free(str(i)) for i in range(6)])
    assert apply_checklist("planner", output) is output


class _Recorder:
    def __init__(self):
        self.seen = {}

    async def generate(self, prompt, context):
        self.seen = {"prompt": prompt, "context": context}
        return AgentOutput(checks=[ChecklistAnswer(check_id="FIN_TAX", status="issue")])


@pytest.mark.asyncio
async def test_generate_role_sends_the_checklist_and_converts_answers():
    llm = _Recorder()
    out = await generate_role(llm, "finance", {"dpr": {}})
    assert llm.seen["prompt"] == PROMPTS["finance"] + CHECKLIST_INSTRUCTION
    assert [c["id"] for c in llm.seen["context"]["checklist"]] == [
        i.id for i in CHECKLISTS["finance"]
    ]
    assert [f.check_id for f in out.findings] == ["FIN_TAX"]
    await generate_role(llm, "planner", {"dpr": {}})
    assert llm.seen["prompt"] == PROMPTS["planner"]
    assert "checklist" not in llm.seen["context"]


def _checklist_finding(check_id="FIN_TAX", role="finance"):
    out = AgentOutput(
        checks=[ChecklistAnswer(check_id=check_id, status="issue", evidence_page=4)]
    )
    return apply_checklist(role, out).findings[0]


def test_the_verifier_cannot_downgrade_a_checklist_finding_but_can_reject_it():
    [f] = assign_ids([_checklist_finding()])
    kept, _ = apply_verdicts(
        [f],
        [
            Verdict(
                finding_id="F01",
                decision="downgrade",
                reason="minor",
                new_severity="low",
            )
        ],
    )
    assert kept[0].severity == "medium"
    kept, rejected = apply_verdicts(
        [f], [Verdict(finding_id="F01", decision="reject", reason="quote not on page")]
    )
    assert kept == [] and rejected[0].check_id == "FIN_TAX"


def test_a_free_form_restatement_does_not_re_rate_a_checklist_finding():
    checklist = _checklist_finding()
    restated = checklist.model_copy(
        update={
            "check_id": None,
            "severity": "critical",
            "priority": None,
            "explanation": "A much longer restatement of the same tax issue.",
        }
    )
    [merged] = consolidate([restated, checklist])
    assert merged.check_id == "FIN_TAX"
    assert merged.severity == "medium" and merged.merged_duplicate_count == 2


def test_stability_reports_checklist_agreement():
    def run(statuses):
        return {
            "findings": [],
            "agent_outputs": {
                "risk": {
                    "checks": [
                        {"check_id": k, "status": s} for k, s in statuses.items()
                    ]
                }
            },
        }

    st = stability(
        [
            run({"RISK_SINGLE_ASSET": "issue", "RISK_SENSITIVITY": "ok"}),
            run({"RISK_SINGLE_ASSET": "issue", "RISK_SENSITIVITY": "issue"}),
        ]
    )
    assert st["checklist_answer_agreement"] == 0.5
    assert st["checklist_flagged_jaccard"] == 0.5
    assert (
        stability([{"findings": []}, {"findings": []}])["checklist_answer_agreement"]
        is None
    )


def test_cannot_tell_on_a_presence_item_means_it_is_missing():
    out = AgentOutput(
        checks=[
            ChecklistAnswer(check_id="DOC_QUOTATION", status="cannot_tell"),
            ChecklistAnswer(check_id="DOC_PREMISES", status="cannot_tell"),
        ]
    )
    [finding] = apply_checklist("document", out).findings
    assert finding.check_id == "DOC_QUOTATION" and finding.priority == "must_fix"
    assert finding.explanation == "Not found anywhere in the DPR."


def test_a_checklist_finding_absorbs_a_free_form_duplicate_whichever_way_merged():
    """Seen live: the verifier merged the must-fix quotation item into a
    free-form should-fix finding, and the must-fix disappeared."""
    checklist = _checklist_finding("DOC_QUOTATION", "document")
    free = _free("Missing vendor quotations", "high")
    candidates = assign_ids([checklist, free])
    kept, _ = apply_verdicts(
        candidates,
        [
            Verdict(
                finding_id="F01", decision="merge", duplicate_of="F02", reason="same"
            )
        ],
    )
    [only] = kept
    assert only.check_id == "DOC_QUOTATION" and only.priority == "must_fix"
    assert only.severity == "high" and only.merged_duplicate_count == 2


def test_calculated_beats_checklist_and_two_checklist_items_never_merge():
    from app.services.dpr_agentic.schemas import Finding as F

    calc = F(
        category="dscr",
        severity="medium",
        title="Stated DSCR",
        explanation="x",
        suggested_action="y",
        provenance="calculated",
    )
    a, b = _checklist_finding("FIN_TAX"), _checklist_finding("FIN_DEPRECIATION")
    cands = assign_ids([calc, a, b])
    kept, _ = apply_verdicts(
        cands,
        [
            Verdict(finding_id="F01", decision="merge", duplicate_of="F02", reason="r"),
            Verdict(finding_id="F03", decision="merge", duplicate_of="F02", reason="r"),
        ],
    )
    assert [k.finding_id for k in kept] == ["F01", "F03"]
    assert kept[0].provenance == "calculated" and kept[0].merged_duplicate_count == 2


@pytest.mark.asyncio
async def test_only_checklist_roles_are_offered_the_checks_field():
    """Offered to every role, the planner/verifier/correction invented
    checklists of their own (seen live)."""
    import json

    import httpx

    from app.services.dpr_agentic.gemini import GeminiAgentClient

    schemas = []

    def handler(request):
        body = json.loads(request.content)
        schemas.append(set(body["generationConfig"]["responseSchema"]["properties"]))
        text = '{"summary": "s", "findings": [], "unresolved_questions": []}'
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"finishReason": "STOP", "content": {"parts": [{"text": text}]}}
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gemini = GeminiAgentClient(client, "fake-key", "gemini-3.8-flash")
        await generate_role(gemini, "risk", {"dpr": {}})
        await generate_role(gemini, "planner", {"dpr": {}})
    assert "checks" in schemas[0] and "checks" not in schemas[1]


def test_the_risk_agent_sees_dpr_pages_for_its_checklist():
    from app.services.dpr_agentic.context_router import select_context
    from app.services.dpr_agentic.schemas import AnalysisInput, SourcePage

    inp = AnalysisInput(
        service_request_id="s",
        wallet_document_id="w",
        document_sha256="a" * 64,
        pages=[
            SourcePage(page=1, text="Introduction"),
            SourcePage(
                page=7,
                text="Technical: one combine harvester, "
                "operating days 120, machinery cost",
            ),
        ],
    )
    ctx = select_context("risk", inp, {}, [])
    assert [p["page"] for p in ctx["dpr"]["source_pages"]] == [7]


def test_new_extraction_steps_think_low_and_are_capped():
    """Seen live: without entries they ran at MEDIUM thinking, uncapped,
    ~7k output tokens per call for a small copy-as-printed schema."""
    from app.services.dpr_agentic.gemini import _ROLE_OUTPUT_CAP, _ROLE_THINKING_LEVEL

    for role in ("financial_unit_economics", "financial_harvester"):
        assert _ROLE_THINKING_LEVEL[role] == "LOW"
        assert _ROLE_OUTPUT_CAP[role] == 4096
    assert "Leave explanation and evidence empty" in CHECKLIST_INSTRUCTION


def test_demand_evidence_is_a_sector_presence_item():
    from app.services.dpr_agentic.checklists import CHECKLISTS

    item = next(i for i in CHECKLISTS["sector"] if i.id == "SEC_DEMAND_EVIDENCE")
    assert item.absence and item.priority == "should_fix"
    assert "letters of intent" in item.question
