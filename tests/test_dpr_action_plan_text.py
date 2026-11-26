"""Phase 4 of the 2.x layout: every finding carries its own "why it
matters", "your DPR" and "should be" for the action plan, and the
documents table answers from report-only checklist items."""

from types import SimpleNamespace

from app.services.dpr_agentic import cam_review
from app.services.dpr_agentic.cam_adapter import build_snapshot_from_analysis
from app.services.dpr_agentic.cam_render_v2 import build_action_plan
from app.services.dpr_agentic.checklists import (
    CHECKLIST_INSTRUCTION,
    CHECKLISTS,
    WHY_AND_SHOULD_BE,
    apply_checklist,
)
from app.services.dpr_agentic.context_router import select_context
from app.services.dpr_agentic.financial_calc import (
    CalculationResult,
    findings_from_calculations,
)
from app.services.dpr_agentic.schemas import (
    AgentOutput,
    AnalysisResult,
    ChecklistAnswer,
    Finding,
)
from tests.test_dpr_benchmarks import checks, ev, polyhouse
from tests.test_dpr_schemes import claimed, fin, run_scheme_checks


def test_every_checklist_item_that_makes_findings_has_text():
    for items in CHECKLISTS.values():
        for item in items:
            if not item.report_only:
                assert item.id in WHY_AND_SHOULD_BE, item.id
    assert "why_it_matters" in CHECKLIST_INSTRUCTION


def test_checklist_findings_carry_why_should_be_and_the_dpr_words():
    out = apply_checklist(
        "document",
        AgentOutput(
            checks=[
                ChecklistAnswer(check_id="DOC_QUOTATION", status="cannot_tell"),
                ChecklistAnswer(
                    check_id="DOC_IDENTITY",
                    status="issue",
                    explanation="Another name on page 9.",
                    evidence_page=9,
                    evidence_quote="Name of the applicant: Shri Ramesh Kumar "
                    "son of late Shri Mohan Lal resident of village X",
                ),
                # Report-only: feeds the documents table, never a finding.
                ChecklistAnswer(check_id="DOC_REGISTRATION", status="issue"),
            ]
        ),
    )
    by_id = {f.check_id: f for f in out.findings}
    assert set(by_id) == {"DOC_QUOTATION", "DOC_IDENTITY"}
    quotation = by_id["DOC_QUOTATION"]
    assert quotation.dpr_stated_value == "Not in the DPR"
    assert quotation.why_it_matters == WHY_AND_SHOULD_BE["DOC_QUOTATION"][0]
    assert quotation.should_be == "Current quotation, total = project cost"
    assert by_id["DOC_IDENTITY"].dpr_stated_value.endswith(" …")


def test_calculated_findings_print_their_own_numbers():
    [average] = findings_from_calculations(
        [CalculationResult("dscr_average", 1.12, 1.25, False, -0.13, "7 years")]
    )
    assert average.dpr_stated_value == "Average DSCR 1.12"
    assert average.should_be == "≥ 1.25"
    assert average.why_it_matters.startswith("Banks usually require")
    # Either side of a total may be wrong: the fix is named, not a figure.
    [cost] = findings_from_calculations(
        [CalculationResult("means_of_finance_total", 105, 70, False, 35)]
    )
    assert cost.dpr_stated_value == "Total 70; items add to 105"
    assert cost.should_be == "Items that add up to the total"
    assert cost.why_it_matters == "Funding that does not add up is queried"


def test_benchmark_findings_print_the_figure_and_the_limit():
    _, [finding], _ = checks(polyhouse(price_per_unit_rupees=ev(12, 30)))
    assert finding.dpr_stated_value == "12 ₹/unit"
    assert finding.should_be.startswith("≤ 8")
    assert finding.why_it_matters


def test_scheme_findings_print_the_rule_and_the_dpr():
    [finding], _ = run_scheme_checks(
        claimed("PMFME"), fin(40, {"Promoter": 4, "PMFME subsidy": 12, "Loan": 24})
    )
    assert finding.dpr_stated_value == "12 lakh"
    assert finding.should_be.startswith("At most 10 lakh")


def _result(findings=(), checks_=()):
    return AnalysisResult(
        service_request_id="SR",
        wallet_document_id="W",
        document_sha256="a" * 64,
        status="completed",
        agent_outputs={"document": AgentOutput(checks=list(checks_))},
        findings=list(findings),
        unresolved_questions=[],
    )


def test_the_action_plan_prints_the_finding_text():
    f = Finding(
        category="market",
        severity="medium",
        title="Price unsupported",
        explanation="x",
        suggested_action="y",
        why_it_matters="Revenue may be overstated by a third",
        dpr_stated_value="₹ 2,500 an acre",
        should_be="₹ 2,000 an acre",
    )
    f.priority = "should_fix"
    snap = build_snapshot_from_analysis(
        _result([f]), report_id="r", report_version=1, analysis_run_id="run"
    )
    [row] = build_action_plan(snap)["rows"]
    assert row["why"] == "Revenue may be overstated by a third"
    # Plain sentences, not the raw quote and terse target (2026-11-25).
    assert (row["found"], row["todo"]) == ("x", "y")


def test_documents_table_uses_the_report_only_answers():
    result = _result(
        checks_=[
            ChecklistAnswer(check_id="DOC_REGISTRATION", status="ok"),
            ChecklistAnswer(check_id="DOC_PAST_FINANCIALS", status="not_applicable"),
            ChecklistAnswer(check_id="DOC_SCHEME_REGISTRATION", status="issue"),
        ]
    )
    docs = {d.document: d for d in cam_review.document_checklist(result)}
    assert docs["Entity registration, deed / MoA, Udyam"].status == "available"
    itr = docs["Last 3 years' ITR and financials"]
    assert itr.status == "to_confirm" and "not needed" in itr.action
    assert docs["Scheme portal registration ID"].status == "missing"
    assert docs["Statutory approvals / NOCs"].status == "to_confirm"


def test_document_agent_sees_registration_lines_from_any_page():
    pages = [SimpleNamespace(page=n, text=f"Page {n} narrative") for n in range(1, 12)]
    pages[6] = SimpleNamespace(page=7, text="Udyam Registration No UDYAM-MP-12-0001")
    inp = SimpleNamespace(pages=pages, project_context={})
    ctx = select_context("document", inp, {}, [])
    mentions = ctx["dpr"]["document_mentions"]
    assert mentions == [
        {"page": 7, "lines": ["Udyam Registration No UDYAM-MP-12-0001"]}
    ]


def _free(title, category="risk"):
    return Finding(
        category=category,
        severity="high",
        title=title,
        explanation="x",
        suggested_action="y",
        evidence_quote=f"quote for {title}",
    )


def _checklist_finding(check_id):
    out = apply_checklist(
        "risk" if check_id.startswith("RISK") else "sector",
        AgentOutput(checks=[ChecklistAnswer(check_id=check_id, status="issue")]),
    )
    return out.findings[0]


def test_restatements_of_checklist_items_join_them():
    """Seen live on 912a: the verifier re-added four checklist items under
    new titles, so the customer saw each twice."""
    from app.services.dpr_agentic.finding_consolidation import consolidate
    from app.services.dpr_agentic.finding_judge import absorb_restatements

    demand = _checklist_finding("SEC_DEMAND_EVIDENCE")
    asset = _checklist_finding("RISK_SINGLE_ASSET")
    new = [
        _free("No documented proof of demand", "market"),
        _free("Single asset dependence with operational risks"),
        _free("Debtors mismatch between schedules", "inconsistency"),
    ]
    out = absorb_restatements([demand, asset], new)
    assert [f.title for f in out] == [
        demand.title,
        asset.title,
        "Debtors mismatch between schedules",
    ]
    assert out[0].merged_duplicate_count == 2
    assert "quote for No documented proof of demand" in out[0].explanation
    # The same in consolidate, whichever comes first.
    merged = consolidate([new[1], asset, new[2]])
    assert [f.title for f in merged] == [
        asset.title,
        "Debtors mismatch between schedules",
    ]
    assert merged[0].check_id == "RISK_SINGLE_ASSET"


def test_documents_questions_do_not_move_the_readiness_score():
    base = [ChecklistAnswer(check_id="DOC_IDENTITY", status="ok")]
    extra = base + [ChecklistAnswer(check_id="DOC_REGISTRATION", status="issue")]
    assert cam_review.readiness_score(_result(checks_=base)) == (
        cam_review.readiness_score(_result(checks_=extra))
    )


def test_informational_findings_stay_out_of_the_action_plan():
    info = Finding(
        category="dscr",
        severity="info",
        title="DSCR YEAR 1: 2.4",
        explanation="x",
        suggested_action="No action needed from this figure alone.",
    )
    info.priority = "good_to_fix"
    snap = build_snapshot_from_analysis(
        _result([info]), report_id="r", report_version=1, analysis_run_id="run"
    )
    assert build_action_plan(snap)["rows"] == []
    assert [f.title for f in snap.findings] == ["DSCR YEAR 1: 2.4"]  # register


def test_agent_restatements_of_code_findings_join_them():
    from app.services.dpr_agentic.finding_consolidation import consolidate

    [calc] = findings_from_calculations(
        [CalculationResult("means_of_finance_total", 105, 70, False, 35)]
    )
    agent = _free("Means of Finance summation error and project cost contradiction")
    other = _free("Debtors mismatch between schedules", "inconsistency")
    merged = consolidate([agent, other, calc])
    assert [f.title for f in merged] == [calc.title, other.title]
    assert merged[0].provenance == "calculated"
