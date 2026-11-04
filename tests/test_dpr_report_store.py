"""report_store.py: a stored report is immutable once written.

Uses the real Postgres fixtures (tests/conftest.py) -- see its docstring
for how to point DPR_TEST_DATABASE_URL at a throwaway database; these
tests skip (not fail) without one.
"""

import pytest

from app.services.dpr_agentic.jobs import claim, submit
from app.services.dpr_agentic.report_store import (
    ReportAlreadyStoredError,
    get_report,
    store_report,
)
from app.services.dpr_agentic.schemas import AgentOutput, AnalysisResult


def _report(sha: str) -> AnalysisResult:
    return AnalysisResult(
        service_request_id="SR-1",
        wallet_document_id="W-1",
        document_sha256=sha,
        status="completed",
        agent_outputs={"document": AgentOutput(summary="ok")},
        findings=[],
        unresolved_questions=[],
    )


def test_store_report_round_trips(dpr_pg):
    run_id, _ = submit(
        dpr_pg,
        service_request_id="SR-1",
        document_sha256="a" * 64,
        source_reference="W-1",
    )
    claim(dpr_pg, run_id)
    store_report(dpr_pg, run_id, _report("a" * 64))
    assert get_report(dpr_pg, run_id).document_sha256 == "a" * 64


def test_store_report_refuses_to_overwrite_an_existing_report(dpr_pg):
    """Regression: store_report() used `ON CONFLICT (run_id) DO UPDATE`,
    which would silently replace a previously stored report -- a violation
    of "never overwrite a previous report version" (Section 16). A second
    store for the same run_id must now raise, and the original report
    must survive untouched."""
    run_id, _ = submit(
        dpr_pg,
        service_request_id="SR-1",
        document_sha256="a" * 64,
        source_reference="W-1",
    )
    claim(dpr_pg, run_id)
    store_report(dpr_pg, run_id, _report("a" * 64))

    with pytest.raises(ReportAlreadyStoredError):
        store_report(dpr_pg, run_id, _report("b" * 64))

    # The original report must survive untouched -- not partially
    # overwritten, not deleted.
    assert get_report(dpr_pg, run_id).document_sha256 == "a" * 64
