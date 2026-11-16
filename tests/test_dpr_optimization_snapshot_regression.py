"""Offline regression tests for optimized DPR/CAM pipeline.

Run with: pytest -q tests/test_dpr_optimization_snapshot_regression.py
These tests use synthetic DPR text; no Wallet, Gemini or PostgreSQL.
"""
from __future__ import annotations

import json
from hashlib import sha256

from app.services.dpr_agentic.context_router import (
    financial_extraction_context, select_context, select_evidence_pages,
)
from app.services.dpr_agentic.financial_schema import (
    ProjectFinancials, financial_extraction_completeness,
)
from app.services.dpr_agentic.schemas import AnalysisInput, AnalysisResult, AgentOutput, SourcePage, Finding
from app.services.dpr_agentic.cam_adapter import build_snapshot_from_analysis
from app.services.dpr_agentic.cam_render import build_page1_context, build_page4_context


def _inp():
    return AnalysisInput(
        service_request_id="TEST", wallet_document_id="doc", document_sha256="a"*64,
        pages=[
            SourcePage(page=1, text="PROJECT COST. Total project cost is 29.00 lakh"),
            SourcePage(page=2, text="MEANS OF FINANCE. Bank loan 21.75 lakh, promoter 7.25 lakh"),
            SourcePage(page=3, text="Repayment schedule Year 1 principal 3.11 interest 1.95"),
            SourcePage(page=4, text="Harvesting capacity 10 acres per day in local season"),
            SourcePage(page=5, text="Profit and Loss: revenue 24 lakh depreciation 3 lakh"),
        ],
    )


def _result(financials: dict, findings=None, questions=None):
    return AnalysisResult(
        service_request_id="TEST", wallet_document_id="doc", document_sha256="a"*64,
        status="completed", agent_outputs={}, findings=findings or [],
        unresolved_questions=questions or [], extracted_financials=financials,
    )


def test_financial_extraction_is_not_empty_success():
    missing = financial_extraction_completeness(ProjectFinancials())
    assert missing["has_financial_data"] is False
    assert "project_cost" in missing["missing_sections"]


def test_financial_context_retains_all_named_sections_and_page_numbers():
    source = _inp()
    ctx = financial_extraction_context(source)
    pages = {p["page"] for p in ctx["pages"]}
    assert {1, 2, 3, 5} <= pages
    # Neighbor retained for a potentially continued Year-1 schedule.
    assert 4 in pages
    assert all("page" in item for item in ctx["section_index"])


def test_finance_and_sector_are_distinct():
    source = _inp()
    finance = select_context("finance", source, {}, [])
    sector = select_context("sector", source, {}, [])
    assert 2 in {p["page"] for p in finance["dpr"]["pages"]}
    assert 4 in {p["page"] for p in sector["dpr"]["pages"]}
    # Risk sees a bounded set of operations/cost pages (its checklist must
    # be judged from the DPR), never the financial statements wholesale.
    risk_pages = select_context("risk", source, {}, [])["dpr"]["source_pages"]
    assert len(risk_pages) <= 6
    assert all(len(p["text"]) <= 3500 for p in risk_pages)


def test_verifier_has_original_page_evidence():
    source = _inp()
    prior = {"finance": {"findings": [{"title": "Cost mismatch", "category":"funding",
                                "severity":"high", "evidence_page":2, "evidence_quote":"Bank loan 21.75 lakh"}]}}
    ctx = select_context("verifier", source, prior, [])
    assert any(p["page"] == 2 and "Bank loan" in p["text"] for p in ctx["dpr"]["source_pages"])


def test_non_harvester_repayment_schedule_populates_even_without_years():
    extracted = ProjectFinancials.model_validate({
        "cost_of_project_stated_total": 29,
        "term_loan_amount": {"value": 21.75, "page": 2, "status": "extracted"},
        "repayment_table": [{"year": 1, "principal_due": {"value":3.11,"page":3,"status":"extracted"},
                              "interest_due": {"value":1.95,"page":3,"status":"extracted"}}],
    })
    snap = build_snapshot_from_analysis(_result(extracted.model_dump(mode="json")),
                                       report_id="report", report_version=1, analysis_run_id="run")
    assert snap.project_cost.dpr_reported_total.amount == 29
    assert snap.means_of_finance.requested_loan.amount == 21.75
    assert str(snap.repayment_schedule[0].total_debt_service.amount) == "5.06"
    assert snap.financial_ratios.average_dscr is None
    assert "₹ 21.75" in build_page1_context(snap)["loan"]["requested_amount"]


def test_empty_extraction_marks_cam_insufficient():
    snap = build_snapshot_from_analysis(_result({}, questions=["FINANCIAL_EXTRACTION_INCOMPLETE: empty"]),
                                       report_id="report", report_version=1, analysis_run_id="run")
    assert snap.readiness_assessment.status.value == "INSUFFICIENT_INFORMATION"


def test_identity_conflict_is_not_unchecked():
    finding = Finding(category="identity_mismatch", severity="critical",
        title="Applicant name inconsistent", explanation="Names conflict",
        suggested_action="Verify KYC")
    snap = build_snapshot_from_analysis(_result({}, findings=[finding]),
                                       report_id="report", report_version=1, analysis_run_id="run")
    checks = {c["title"]: c["status"] for c in build_page1_context(snap)["checks"]}
    assert "conflict" in checks["Identity and KYC"].casefold()


def test_snapshot_is_validatable_json_and_checksum():
    snap = build_snapshot_from_analysis(_result({}), report_id="report", report_version=4, analysis_run_id="run")
    payload = snap.model_dump_json()
    digest = sha256(json.dumps(json.loads(payload), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert len(digest) == 64
    assert type(snap).model_validate_json(payload).report_version == 4


def test_stored_snapshot_is_read_without_rebuilding(monkeypatch):
    from app.services.dpr_agentic import report_store
    report = build_snapshot_from_analysis(_result({}), report_id="ref", report_version=2,
                                          analysis_run_id="run")
    packed = json.loads(report.model_dump_json())
    canonical = json.dumps(packed, sort_keys=True, separators=(",", ":"))
    digest = sha256(canonical.encode()).hexdigest()

    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            pass
        def execute(self, statement, params):
            assert params == {"ref": "ref", "version": 2}
            class Result:
                def first(self):
                    return (json.dumps(packed), digest)
            return Result()
    class Engine:
        def connect(self):
            return Connection()

    stored = report_store.get_cam_snapshot(Engine(), "ref", version=2)
    assert stored.model_dump(mode="json") == report.model_dump(mode="json")
    assert stored.report_version == 2


def test_canonical_snapshot_checksum_tampering_detected():
    from app.services.dpr_agentic import report_store
    report = build_snapshot_from_analysis(_result({}), report_id="ref", report_version=1,
                                          analysis_run_id="run")
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, *args):
            class Result:
                def first(self): return (report.model_dump_json(), "bad-digest")
            return Result()
    class Engine:
        def connect(self): return Conn()
    try:
        report_store.get_cam_snapshot(Engine(), "ref", version=1)
    except ValueError as exc:
        assert "checksum" in str(exc)
    else:
        raise AssertionError("corrupt canonical CAM snapshot was accepted")
