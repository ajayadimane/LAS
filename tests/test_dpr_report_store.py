"""report_store.py: append-only report versioning scoped to the service
(migrations 005, 006).

Every store_report() call appends a new version for the run's
source_reference; no row is ever updated or deleted, so a previous report
version can never be overwritten (Section 16). Versions are scoped to
source_reference, not run_id, because re-analysis after DPR corrections
submits a brand-new run_id (see jobs.submit()'s idempotency_key, which
includes document_sha256) -- scoping to run_id would restart history at v1
on every correction instead of continuing as v2, v3.

Uses the real Postgres fixtures (tests/conftest.py) -- see its docstring
for how to point DPR_TEST_DATABASE_URL at a throwaway database; these
tests skip (not fail) without one.
"""

import threading
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.services.dpr_agentic.jobs import claim, finish, submit
from app.services.dpr_agentic.report_store import (
    get_report,
    list_report_versions,
    store_report,
)
from app.services.dpr_agentic.schemas import AgentOutput, AnalysisResult


def _report(sha: str, *, status: str = "completed") -> AnalysisResult:
    return AnalysisResult(
        service_request_id="SR-1",
        wallet_document_id="W-1",
        document_sha256=sha,
        status=status,
        agent_outputs={"document": AgentOutput(summary="ok")},
        findings=[],
        unresolved_questions=[],
    )


def _submit(dpr_pg, *, source_reference: str, sha: str = "a" * 64) -> str:
    run_id, _ = submit(
        dpr_pg,
        service_request_id="SR-1",
        document_sha256=sha,
        source_reference=source_reference,
    )
    return run_id


def test_store_report_round_trips(dpr_pg):
    run_id = _submit(dpr_pg, source_reference="svc:1")
    claim(dpr_pg, run_id)
    version = store_report(dpr_pg, run_id, _report("a" * 64))
    assert version == 1
    assert get_report(dpr_pg, "svc:1").document_sha256 == "a" * 64


def test_a_second_store_appends_a_new_version_never_overwrites_the_first(dpr_pg):
    """Regression: store_report() originally used
    `ON CONFLICT (run_id) DO UPDATE`, which would silently replace a
    previously stored report -- a violation of "never overwrite a
    previous report version" (Section 16). This is the current form: a
    second store with genuinely different content appends version 2,
    leaving version 1 exactly as it was."""
    run_id = _submit(dpr_pg, source_reference="svc:1")
    claim(dpr_pg, run_id)
    v1 = store_report(dpr_pg, run_id, _report("a" * 64))
    v2 = store_report(dpr_pg, run_id, _report("b" * 64))
    assert (v1, v2) == (1, 2)

    # The first version survives untouched -- not partially overwritten,
    # not deleted.
    assert get_report(dpr_pg, "svc:1", version=1).document_sha256 == "a" * 64
    assert get_report(dpr_pg, "svc:1", version=2).document_sha256 == "b" * 64
    # get_report() with no version returns the latest.
    assert get_report(dpr_pg, "svc:1").document_sha256 == "b" * 64
    assert list_report_versions(dpr_pg, "svc:1") == [1, 2]


def test_storing_identical_content_for_the_same_run_is_idempotent(dpr_pg):
    """Storing the same run_id with byte-identical report content twice
    (e.g. a retried worker step) returns the existing version instead of
    appending a redundant one -- enforced by UNIQUE (run_id,
    snapshot_sha256), not just an application-level check."""
    run_id = _submit(dpr_pg, source_reference="svc:1")
    claim(dpr_pg, run_id)
    report = _report("a" * 64)
    v1 = store_report(dpr_pg, run_id, report)
    v1_again = store_report(dpr_pg, run_id, report)
    assert (v1, v1_again) == (1, 1)
    assert list_report_versions(dpr_pg, "svc:1") == [1]


def test_get_report_returns_none_for_a_run_with_no_stored_report(dpr_pg):
    _submit(dpr_pg, source_reference="svc:1")
    assert get_report(dpr_pg, "svc:1") is None
    assert list_report_versions(dpr_pg, "svc:1") == []


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
    run_id = _submit(dpr_pg, source_reference="svc:1")
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
    assert list_report_versions(dpr_pg, "svc:1") == [1, 2]
    assert get_report(dpr_pg, "svc:1", version=1).document_sha256 == "a" * 64
    assert get_report(dpr_pg, "svc:1").document_sha256 == "b" * 64


def test_reanalysis_under_a_new_run_id_continues_the_same_version_history(dpr_pg):
    """A DPR correction produces a new document -> a new document_sha256 ->
    a different jobs.submit() idempotency key -> a brand-new run_id (never
    the same run_id as the original). Versions are scoped to
    source_reference specifically so this continues as v2, not a fresh v1
    under the new run_id."""
    ref = "svc:1"
    run_1 = _submit(dpr_pg, source_reference=ref, sha="a" * 64)
    claim(dpr_pg, run_1)
    assert store_report(dpr_pg, run_1, _report("a" * 64)) == 1

    run_2 = _submit(dpr_pg, source_reference=ref, sha="b" * 64)
    assert run_2 != run_1
    claim(dpr_pg, run_2)
    assert store_report(dpr_pg, run_2, _report("b" * 64)) == 2

    assert list_report_versions(dpr_pg, ref) == [1, 2]
    assert get_report(dpr_pg, ref).document_sha256 == "b" * 64


def test_get_report_default_skips_a_version_that_never_finished_rendering(dpr_pg):
    """render_status='PENDING' (an incomplete analysis) is stored for
    audit/history but must not be served as "the" report by default;
    an explicit version lookup still returns it."""
    run_id = _submit(dpr_pg, source_reference="svc:1")
    claim(dpr_pg, run_id)
    store_report(dpr_pg, run_id, _report("a" * 64, status="completed"))
    v2 = store_report(dpr_pg, run_id, _report("b" * 64, status="incomplete"))
    assert v2 == 2

    # Latest overall is v2, but it never rendered, so the default lookup
    # falls back to the latest RENDERED version (v1), not None.
    assert get_report(dpr_pg, "svc:1").document_sha256 == "a" * 64
    assert get_report(dpr_pg, "svc:1", version=2).document_sha256 == "b" * 64


def test_concurrent_writers_with_no_prior_version_retry_past_the_conflict(dpr_pg):
    """Two concurrent first-time stores for the same service can both
    compute "next version = 1" (SELECT ... FOR UPDATE locks nothing when
    there is no existing row to lock), so the UNIQUE (source_reference,
    version) constraint -- not the lock -- is the real arbiter. One writer
    must get version 1 and the other must retry onto version 2; both must
    succeed, and no version number may be produced twice.

    The CAM PDF render is stubbed out for this test: WeasyPrint/cairo/pango
    are not safe to invoke from two threads at once (observed hanging
    indefinitely, not raising), which is irrelevant to what this test
    verifies -- the version-conflict retry -- and scripts/dpr_worker.py
    never calls store_report() from more than one thread in practice."""
    ref = "svc:1"
    run_a = _submit(dpr_pg, source_reference=ref, sha="a" * 64)
    run_b = _submit(dpr_pg, source_reference=ref, sha="b" * 64)
    claim(dpr_pg, run_a)
    claim(dpr_pg, run_b)

    results: dict[str, int] = {}
    barrier = threading.Barrier(2)

    def _store(run_id: str, sha: str, key: str) -> None:
        barrier.wait(timeout=5)
        results[key] = store_report(dpr_pg, run_id, _report(sha))

    with patch(
        "app.services.dpr_agentic.report_store._render_cam_pdf",
        return_value=b"%PDF-stub",
    ):
        t1 = threading.Thread(target=_store, args=(run_a, "a" * 64, "a"))
        t2 = threading.Thread(target=_store, args=(run_b, "b" * 64, "b"))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

    assert sorted(results.values()) == [1, 2]
    assert list_report_versions(dpr_pg, ref) == [1, 2]


def test_a_stored_report_row_cannot_be_updated_or_deleted(dpr_pg):
    """Immutability is enforced in the database (migration 006's trigger),
    independent of this module ever issuing an UPDATE/DELETE itself."""
    run_id = _submit(dpr_pg, source_reference="svc:1")
    claim(dpr_pg, run_id)
    store_report(dpr_pg, run_id, _report("a" * 64))

    with dpr_pg.connect() as conn:
        with pytest.raises(DBAPIError):
            with conn.begin():
                conn.execute(
                    text(
                        "UPDATE dpr_report_artifacts SET snapshot_sha256='x' "
                        "WHERE source_reference='svc:1' AND version=1"
                    )
                )

    with dpr_pg.connect() as conn:
        with pytest.raises(DBAPIError):
            with conn.begin():
                conn.execute(
                    text(
                        "DELETE FROM dpr_report_artifacts "
                        "WHERE source_reference='svc:1' AND version=1"
                    )
                )

    assert get_report(dpr_pg, "svc:1", version=1).document_sha256 == "a" * 64


def test_render_status_may_only_move_forward(dpr_pg):
    run_id = _submit(dpr_pg, source_reference="svc:1")
    claim(dpr_pg, run_id)
    store_report(dpr_pg, run_id, _report("a" * 64, status="incomplete"))

    with dpr_pg.begin() as conn:
        conn.execute(
            text(
                "UPDATE dpr_report_artifacts SET render_status='RENDERED' "
                "WHERE source_reference='svc:1' AND version=1"
            )
        )

    with pytest.raises(DBAPIError):
        with dpr_pg.begin() as conn:
            conn.execute(
                text(
                    "UPDATE dpr_report_artifacts SET render_status='PENDING' "
                    "WHERE source_reference='svc:1' AND version=1"
                )
            )
