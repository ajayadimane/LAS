"""report_store.py: append-only report versioning (migration 005).

Every store_report() call appends a new version; no row is ever updated
or deleted, so a previous report version can never be overwritten
(Section 16). Replaces the earlier ReportAlreadyStoredError approach,
which raised on a second store instead of versioning it -- see
PROGRESS.md milestone 5 for why.

Uses the real Postgres fixtures (tests/conftest.py) -- see its docstring
for how to point DPR_TEST_DATABASE_URL at a throwaway database; these
tests skip (not fail) without one.
"""

from app.services.dpr_agentic.jobs import claim, finish, submit
from app.services.dpr_agentic.report_store import (
    get_report,
    list_report_versions,
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
    version = store_report(dpr_pg, run_id, _report("a" * 64))
    assert version == 1
    assert get_report(dpr_pg, run_id).document_sha256 == "a" * 64


def test_a_second_store_appends_a_new_version_never_overwrites_the_first(dpr_pg):
    """Regression: store_report() originally used
    `ON CONFLICT (run_id) DO UPDATE`, which would silently replace a
    previously stored report -- a violation of "never overwrite a
    previous report version" (Section 16). A later fix raised instead of
    overwriting; this is the final form: a second store for the same
    run_id appends version 2, leaving version 1 exactly as it was."""
    run_id, _ = submit(
        dpr_pg,
        service_request_id="SR-1",
        document_sha256="a" * 64,
        source_reference="W-1",
    )
    claim(dpr_pg, run_id)
    v1 = store_report(dpr_pg, run_id, _report("a" * 64))
    v2 = store_report(dpr_pg, run_id, _report("b" * 64))
    assert (v1, v2) == (1, 2)

    # The first version survives untouched -- not partially overwritten,
    # not deleted.
    assert get_report(dpr_pg, run_id, version=1).document_sha256 == "a" * 64
    assert get_report(dpr_pg, run_id, version=2).document_sha256 == "b" * 64
    # get_report() with no version returns the latest.
    assert get_report(dpr_pg, run_id).document_sha256 == "b" * 64
    assert list_report_versions(dpr_pg, run_id) == [1, 2]


def test_get_report_returns_none_for_a_run_with_no_stored_report(dpr_pg):
    run_id, _ = submit(
        dpr_pg,
        service_request_id="SR-1",
        document_sha256="a" * 64,
        source_reference="W-1",
    )
    assert get_report(dpr_pg, run_id) is None
    assert list_report_versions(dpr_pg, run_id) == []


def test_a_lease_reclaim_race_now_versions_instead_of_failing_the_run(dpr_pg):
    """Reproduces the realistic race ReportAlreadyStoredError used to guard
    against: worker A's lease expires while it is still (slowly, not
    dead) processing; worker B reclaims the same run_id and starts over.
    Worker A finishes first and stores -- version 1. Worker A's finish()
    call is now a no-op (its lease_token is stale; jobs.finish() only
    updates the row for the CURRENT lease_token), so the run stays
    RUNNING under worker B's claim. Worker B finishes its own (redundant)
    run and stores too -- version 2, not an error -- and its finish()
    call succeeds, so the run ends COMPLETED with both versions
    preserved, rather than the old behaviour of ending FAILED despite a
    perfectly good report having been stored underneath it."""
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
    assert store_report(dpr_pg, run_id, _report("a" * 64)) == 1
    assert finish(dpr_pg, run_id, token_a, success=True, artifact_ref=run_id) is False

    # Worker B finishes its own (redundant) run and stores too -- a new
    # version, not a conflict.
    assert store_report(dpr_pg, run_id, _report("b" * 64)) == 2
    assert finish(dpr_pg, run_id, token_b, success=True, artifact_ref=run_id) is True

    # No more claimable (terminal, COMPLETED) -- but unlike the old
    # error-raising behaviour, this is a success, not a failure, and both
    # versions are preserved.
    assert claim(dpr_pg, run_id) is None
    assert list_report_versions(dpr_pg, run_id) == [1, 2]
    assert get_report(dpr_pg, run_id, version=1).document_sha256 == "a" * 64
    assert get_report(dpr_pg, run_id).document_sha256 == "b" * 64
