"""report_store.py: a stored report is immutable once written.

Uses the real Postgres fixtures (tests/conftest.py) -- see its docstring
for how to point DPR_TEST_DATABASE_URL at a throwaway database; these
tests skip (not fail) without one.
"""

import pytest

from app.services.dpr_agentic.jobs import claim, finish, submit
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


def test_report_already_stored_leaves_the_run_terminal_and_unreclaimable(dpr_pg):
    """Reproduces the realistic race this error guards against: worker A's
    lease expires while it is still (slowly, not dead) processing; worker B
    reclaims the same run_id and starts over. Worker A finishes first and
    stores the report -- succeeds. Worker A then calls finish() with its
    now-stale lease token, which no-ops (jobs.finish() only updates the row
    for the CURRENT lease_token), so the run stays RUNNING under worker B's
    claim. Worker B eventually finishes its redundant run and tries to
    store its own report -- raises ReportAlreadyStoredError. This mirrors
    exactly what scripts/dpr_worker.py's run_claimed_analysis() does on any
    exception from store_result(): call jobs.finish(success=False), then
    propagate. The assertions below are what that sequence must guarantee:
    the run ends FAILED (never COMPLETED twice, never silently re-queued),
    claim() can never pick it up again (terminal, not retried), and the
    first-stored report is exactly what survives."""
    run_id, _ = submit(
        dpr_pg,
        service_request_id="SR-1",
        document_sha256="a" * 64,
        source_reference="W-1",
    )
    token_a = claim(dpr_pg, run_id, lease_seconds=0)  # expires immediately
    token_b = claim(dpr_pg, run_id)  # worker B reclaims the expired lease
    assert token_b is not None
    assert token_b != token_a

    # Worker A, unaware it was reclaimed, finishes and stores first.
    store_report(dpr_pg, run_id, _report("a" * 64))
    # Worker A's finish() call is now a no-op: its lease_token no longer
    # matches (worker B's claim overwrote it), so this must not flip the
    # row to COMPLETED/FAILED out from under worker B.
    assert finish(dpr_pg, run_id, token_a, success=True, artifact_ref=run_id) is False

    # Worker B finishes its own (redundant) run and tries to store too.
    with pytest.raises(ReportAlreadyStoredError):
        store_report(dpr_pg, run_id, _report("b" * 64))
    # This is exactly what run_claimed_analysis() does in its except block.
    assert (
        finish(
            dpr_pg,
            run_id,
            token_b,
            success=False,
            error_code="ReportAlreadyStoredError",
        )
        is True
    )

    # Terminal: a FAILED run is never reclaimed, so this can never be
    # retried by any future poll_once() cycle.
    assert claim(dpr_pg, run_id) is None

    # The valid first-stored report survives untouched.
    assert get_report(dpr_pg, run_id).document_sha256 == "a" * 64
