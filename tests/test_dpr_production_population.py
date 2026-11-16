"""Regression for source-grounded CAM population and bounded report output."""
from __future__ import annotations

import asyncio

from app.services.dpr_agentic.cam_adapter import build_snapshot_from_analysis
from app.services.dpr_agentic.cam_render import (
    build_page1_context,
    build_page2_context,
    render_cam_report_pdf,
)
from app.services.dpr_agentic.context_router import select_context
from app.services.dpr_agentic.document_pipeline.pipeline import inspect_document
from app.services.dpr_agentic.document_pipeline.vision import extract_pages_via_vision
from app.services.dpr_agentic.financial_schema import ProjectFinancials
from app.services.dpr_agentic.orchestrator import verify_evidence
from app.services.dpr_agentic.schemas import (
    AgentOutput, AnalysisInput, AnalysisResult, DocumentFact, Finding, SourcePage,
)


def _report(finding_count=0, with_source=True):
    source = AgentOutput(document_facts=[
        DocumentFact(field="borrower_name", value="Mrs. Sushma", page=1, quote="Mrs Sushma"),
        DocumentFact(field="project_name", value="Combine Harvester Custom Hiring Centre", page=1, quote="Combine Harvester Custom Hiring Centre"),
        DocumentFact(field="scheme_claimed", value="AIF", page=1, quote="AIF"),
    ])
    verify_evidence(source, {1: "Applicant Mrs Sushma requests funding for Combine Harvester Custom Hiring Centre under AIF."} if with_source else {1: "Document with no customer identity"})
    financials = ProjectFinancials.model_validate({
        "cost_of_project_stated_total": 30,
        "cost_of_project_components": [{"label": "Harvester", "amount": 30}],
        "means_of_finance_components": [
            {"label": "Promoter contribution", "amount": 7.5},
            {"label": "SBI term loan", "amount": 22.5},
        ],
        "years": [{"year": 1, "revenue": 21.6, "operating_expenses": 15.3,
                   "net_profit": 6.3, "depreciation": 4.5,
                   "interest_on_term_loan": 1.46, "principal_repayment": 3.21,
                   "interest_repayment": 1.46}],
    })
    findings = [Finding(category="inconsistency", severity="high",
                        title=f"Financial issue {i}",
                        explanation="A substantial financial inconsistency may exist and requires verification. " * 4,
                        suggested_action="Reconcile the reported input against the source statement.")
                for i in range(finding_count)]
    return AnalysisResult(
        service_request_id="SR-123", wallet_document_id="docref",
        document_sha256="a"*64, status="completed",
        agent_outputs={"document": source}, findings=findings,
        unresolved_questions=[], extracted_financials=financials.model_dump(mode="json"),
    )


def test_verified_dpr_fields_populate_cam_and_amounts():
    snap = build_snapshot_from_analysis(_report(), report_id="wallet-person-custom-field:9876500001:DPR_ANALYSIS-v1", report_version=1, analysis_run_id="run1")
    assert snap.borrower_profile.borrower_name == "Mrs. Sushma"
    assert snap.project_profile.project_name == "Combine Harvester Custom Hiring Centre"
    assert snap.project_profile.scheme_claimed == "AIF"
    assert snap.project_cost.dpr_reported_total.amount == 30
    assert snap.means_of_finance.requested_loan.amount == 22.5
    assert snap.means_of_finance.promoter_contribution.amount == 7.5
    assert str(snap.financial_projections[0].revenue.amount) == "21.6"
    assert str(snap.repayment_schedule[0].total_debt_service.amount) == "4.67"
    assert snap.repayment_schedule[0].dscr.computed_value is not None
    display = build_page1_context(snap)
    assert display["scheme_claimed"] == "AIF"
    assert "9876500001" not in display["report_reference"]


def test_unverified_name_not_propagated():
    snap = build_snapshot_from_analysis(_report(with_source=False), report_id="ref", report_version=1, analysis_run_id="run1")
    assert snap.borrower_profile.borrower_name is None
    assert snap.project_profile.project_name is None


def test_55_issues_stay_in_json_but_pdf_is_six_pages():
    import pymupdf
    snap = build_snapshot_from_analysis(_report(finding_count=55), report_id="ref", report_version=1, analysis_run_id="run1")
    assert len(snap.findings) == 55
    assert len(build_page2_context(snap)["should_fix"]) == 55
    # The six-page invariant is the 1.x layout's; 2.x pages flow.
    pdf = render_cam_report_pdf(snap.model_copy(update={"schema_version": "1.0.0"}))
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        assert doc.page_count == 6
        assert "Mrs. Sushma" in doc[0].get_text()


def test_context_router_excludes_full_text_for_late_agents():
    inp = AnalysisInput(service_request_id="SR", wallet_document_id="d", document_sha256="a"*64,
                        pages=[SourcePage(page=1, text="Project cost and loan finance total is 30 lakh"),
                               SourcePage(page=2, text="Production capacity 1200 acres")])
    d = select_context("document", inp, {}, [])
    assert len(d["dpr"]["pages"]) == 2
    f = select_context("finance", inp, {}, [])
    assert [p["page"] for p in f["dpr"]["pages"]] == [1]
    r = select_context("report", inp, {"risk": {"summary": "Issue identified"}}, [])
    assert "pages" not in r["dpr"]


def test_vision_page_numbers_are_preserved():
    class Fake:
        async def ocr_page(self, data):
            return data.decode()
    pages = asyncio.run(extract_pages_via_vision(Fake(), [b"text page eight"], page_numbers=[8]))
    assert pages == [{"page": 8, "text": "text page eight"}]


def test_backended_subsidy_not_upfront_funding():
    from app.services.dpr_agentic.cam_adapter import _classify_funding_line
    from app.services.dpr_agentic.cam_snapshot import FinanceSourceClass
    assert _classify_funding_line("Back-ended subsidy", "other") == (FinanceSourceClass.BACK_ENDED_SUBSIDY, False)
    assert _classify_funding_line("SBI term loan", "loan") == (FinanceSourceClass.UPFRONT_LOAN, True)
