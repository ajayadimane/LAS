"""Fixed per-role checklists (checklists.py): same questions every run;
code sets each finding's title, category, severity and priority."""

import re

import pytest

from app.services.dpr_agentic.checklists import (
    CHECKLIST_INSTRUCTION,
    CHECKLISTS,
    MAX_EXTRA_FINDINGS,
    PLAIN_LANGUAGE_INSTRUCTION,
    TOPIC_TERMS,
    WHY_AND_SHOULD_BE,
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
    assert llm.seen["prompt"] == (
        PROMPTS["finance"] + PLAIN_LANGUAGE_INSTRUCTION + CHECKLIST_INSTRUCTION
    )
    assert [c["id"] for c in llm.seen["context"]["checklist"]] == [
        i.id for i in CHECKLISTS["finance"]
    ]
    assert [f.check_id for f in out.findings] == ["FIN_TAX"]
    await generate_role(llm, "planner", {"dpr": {}})
    assert llm.seen["prompt"] == PROMPTS["planner"] + PLAIN_LANGUAGE_INSTRUCTION
    assert "checklist" not in llm.seen["context"]


class _SkipsChecklistOnce:
    """Seen live: an agent returned findings but no checklist answers."""

    def __init__(self, second):
        self.calls = 0
        self.second = second

    async def generate(self, prompt, context):
        self.calls += 1
        if self.calls == 1:
            return AgentOutput(findings=[_free("x")])
        return self.second


def _all_answered(role, status="ok"):
    return AgentOutput(
        checks=[ChecklistAnswer(check_id=i.id, status=status) for i in CHECKLISTS[role]]
    )


@pytest.mark.asyncio
async def test_an_unanswered_checklist_is_asked_once_more():
    llm = _SkipsChecklistOnce(_all_answered("finance", "issue"))
    out = await generate_role(llm, "finance", {"dpr": {}})
    assert llm.calls == 2
    assert {f.check_id for f in out.findings} >= {i.id for i in CHECKLISTS["finance"]}


@pytest.mark.asyncio
async def test_a_worse_retry_keeps_the_first_answer():
    llm = _SkipsChecklistOnce(AgentOutput(findings=[_free("y")]))
    out = await generate_role(llm, "finance", {"dpr": {}})
    assert llm.calls == 2
    assert [f.title for f in out.findings] == ["x"]


@pytest.mark.asyncio
async def test_a_fully_answered_checklist_is_not_asked_again():
    class Once:
        calls = 0

        async def generate(self, prompt, context):
            self.calls += 1
            return _all_answered("sector")

    llm = Once()
    await generate_role(llm, "sector", {"dpr": {}})
    assert llm.calls == 1


def test_the_verifier_judges_checklist_findings_by_their_explanation():
    assert "judge the explanation, not the title" in PROMPTS["verifier"]


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
    # The risk call is asked twice: this fake never answers the checklist.
    assert "checks" in schemas[0] and "checks" in schemas[1]
    assert "checks" not in schemas[2]


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


# --- plain language (user feedback on PSR/2026/000010, 2026-11-25) ----------

_JARGON = re.compile(
    r"pro[\s-]*forma|collateral|\bDSCR\b|\bP&L\b|sensitivity|premises|"
    r"evidenced|unsanctioned|\bpromoter\b|reconcile",
    re.I,
)


def _finding_items():
    return [i for items in CHECKLISTS.values() for i in items if not i.report_only]


def test_every_checklist_finding_reads_in_plain_language():
    for item in _finding_items():
        why, _ = WHY_AND_SHOULD_BE[item.id]
        for text in (item.title, why, item.action):
            assert not _JARGON.search(text), (item.id, text)
        # Sentence case, not Title Case.
        words = item.title.split()
        assert sum(w[:1].isupper() for w in words[1:]) <= 1, item.title


def test_restated_insurance_and_wage_findings_fold_into_checklist_topics():
    assert TOPIC_TERMS["RISK_SINGLE_ASSET"].search(
        "Single High-Value Asset Risk Without Stated Insurance"
    )
    assert any(
        pattern.search("Omission of Machine Insurance and Operator Wages")
        for pattern in TOPIC_TERMS.values()
    )


@pytest.mark.asyncio
async def test_the_verifier_is_asked_for_plain_language_too():
    from app.services.dpr_agentic.orchestrator import judge_role

    class Judge:
        async def judge(self, prompt, context):
            self.prompt = prompt
            return AgentOutput()

    llm = Judge()
    await judge_role(llm, {})
    assert llm.prompt.endswith(PLAIN_LANGUAGE_INSTRUCTION)


# --- items added 2026-11-25: recurring topics asked every run ---------------

NEW_ITEMS = (
    "DOC_QUOTATION_FIRM",
    "DOC_ASSET_SAME",
    "FIN_YEARS_CURRENT",
    "FIN_COST_COMPLETE",
    "FIN_LOAN_REPAID",
    "FIN_INTEREST_SUBSIDY",
    "FIN_REVENUE_BUILT_UP",
    "SEC_FUEL_REALISTIC",
    "SEC_MAINTENANCE_RISES",
)


def _topic_of(title):
    from app.services.dpr_agentic.checklists import TOPIC_PRECEDENCE

    hits = [k for k, p in TOPIC_TERMS.items() if p.search(title)]
    return min(hits, key=TOPIC_PRECEDENCE.get) if hits else None


def test_every_new_item_has_wording_and_topic_words():
    items = {i.id: i for items in CHECKLISTS.values() for i in items}
    for item_id in NEW_ITEMS:
        assert item_id in items
        assert item_id in WHY_AND_SHOULD_BE
        assert item_id in TOPIC_TERMS


@pytest.mark.parametrize(
    "title, item_id",
    [
        # Real free-form titles from saved eval runs.
        (
            "Indicative Ex-Works Quotation Lacks Price Firmness and Freight",
            "DOC_QUOTATION_FIRM",
        ),
        (
            "Harvester quotation is indicative and subject to price changes",
            "DOC_QUOTATION_FIRM",
        ),
        ("Vendor quotation or pro-forma invoice missing", "DOC_QUOTATION"),
        ("Conflicting Harvester Model Specifications", "DOC_ASSET_SAME"),
        (
            "Harvester Model Discrepancy Between Project Summary and Asset Quotation",
            "DOC_ASSET_SAME",
        ),
        ("Outdated Base Financial Years in Operational Schedules", "FIN_YEARS_CURRENT"),
        ("expense and balance sheet tables skip alternate years", "FIN_YEARS_CURRENT"),
        ("GPS and fuel sensors omitted from project cost", "FIN_COST_COMPLETE"),
        (
            "Equipment freight and transit costs omitted from project cost",
            "FIN_COST_COMPLETE",
        ),
        ("Incomplete Term Loan Amortization", "FIN_LOAN_REPAID"),
        ("Negative debt amortization during off-season months", "FIN_LOAN_REPAID"),
        (
            "Interest subsidy is assumed without specifying any government scheme",
            "FIN_INTEREST_SUBSIDY",
        ),
        (
            "Revenue projection lacks a detailed calculation buildup",
            "FIN_REVENUE_BUILT_UP",
        ),
        (
            "Aggressive dual escalation of acreage and hiring tariffs",
            "FIN_REVENUE_BUILT_UP",
        ),
        (
            "Understated fuel consumption rate for heavy combine harvester",
            "SEC_FUEL_REALISTIC",
        ),
        ("Fixed maintenance budget ignores machine aging", "SEC_MAINTENANCE_RISES"),
        # 2026-11-26 run: each sat beside its item's own finding.
        (
            "Machinery price quote excludes transport and transit insurance",
            "DOC_QUOTATION_FIRM",
        ),
        ("Machinery make and specifications are not mentioned", "DOC_ASSET_SAME"),
        ("Revenue does not show billing rates or acreage", "FIN_REVENUE_BUILT_UP"),
        # 2026-11-26 second double run.
        ("Term loan is not fully paid off in seven years", "FIN_LOAN_REPAID"),
        (
            "Variable cost per acre conflicts across your report",
            "FIN_STATEMENTS_CONSISTENT",
        ),
        (
            "Stated average DSCR conflicts with project tables",
            "FIN_STATEMENTS_CONSISTENT",
        ),
        (
            "Direct variable cost differs between break-even and cost sheet",
            "FIN_BREAKEVEN_BASIS",
        ),
        # Kept off the new items.
        ("Project premises or shed arrangements not evidenced", "DOC_PREMISES"),
    ],
)
def test_restated_titles_land_on_the_most_specific_item(title, item_id):
    assert _topic_of(title) == item_id


def test_amortization_wording_about_disagreeing_tables_is_not_an_unpaid_loan():
    assert not TOPIC_TERMS["FIN_LOAN_REPAID"].search(
        "Balance sheet loan liability conflicts with amortization schedule"
    )


def test_new_items_do_not_claim_the_other_items_titles():
    titles = {i.id: i.title for items in CHECKLISTS.values() for i in items}
    for item_id in NEW_ITEMS:
        for other, title in titles.items():
            if other != item_id:
                assert not TOPIC_TERMS[item_id].search(title), (item_id, title)


def test_the_years_question_goes_to_the_agent_that_sees_the_tables():
    # 2026-11-25: asked of the document agent (opening and quotation pages
    # only) it answered "ok" once and "cannot_tell" once for the same DPR.
    assert "FIN_YEARS_CURRENT" in {i.id for i in CHECKLISTS["finance"]}
    assert "FIN_YEARS_CURRENT" not in {i.id for i in CHECKLISTS["document"]}


def test_the_document_agent_sees_machine_model_lines_from_every_page():
    from types import SimpleNamespace

    from app.services.dpr_agentic.context_router import select_context

    pages = [SimpleNamespace(page=n, text=f"Page {n} narrative") for n in range(1, 12)]
    pages[6] = SimpleNamespace(page=7, text="Harvester model: DASMESH-7100")
    ctx = select_context(
        "document", SimpleNamespace(pages=pages, project_context={}), {}, []
    )
    lines = {m["page"]: m["lines"] for m in ctx["dpr"]["identity_mentions"]}
    assert lines[7] == ["Harvester model: DASMESH-7100"]


@pytest.mark.asyncio
async def test_code_answered_items_are_not_asked_and_win():
    llm = _Recorder()
    code = {"FIN_TAX": ChecklistAnswer(check_id="FIN_TAX", status="ok")}
    out = await generate_role(llm, "finance", {"dpr": {}}, code)
    asked = [c["id"] for c in llm.seen["context"]["checklist"]]
    assert "FIN_TAX" not in asked
    # The agent's "issue" for FIN_TAX loses to code's "ok".
    assert "FIN_TAX" not in {f.check_id for f in out.findings}
