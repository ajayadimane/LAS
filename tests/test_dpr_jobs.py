"""Integration tests for the durable job registry (app/services/dpr_agentic/jobs.py).

Needs a real Postgres: the SQL (ON CONFLICT ... RETURNING, CURRENT_TIMESTAMP +
interval arithmetic) is not portable to SQLite. See tests/conftest.py for how
to point DPR_TEST_DATABASE_URL at one; otherwise these are skipped.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.services.dpr_agentic.jobs import (
    claim,
    fail_stuck_run,
    finish,
    get_run,
    list_claimable,
    reap_exhausted,
    renew_lease,
    submit,
)


def _submit(engine, **overrides):
    defaults = dict(
        service_request_id="SR-1",
        document_sha256="a" * 64,
        source_reference="wallet-person-custom-field:9000000013:DPR_ANALYSIS",
    )
    defaults.update(overrides)
    return submit(engine, **defaults)


def test_submit_is_idempotent_on_the_same_key(dpr_pg):
    run_id1, created1 = _submit(dpr_pg)
    run_id2, created2 = _submit(dpr_pg)
    assert created1 is True
    assert created2 is False
    assert run_id1 == run_id2


def test_submit_with_a_different_key_is_a_new_run(dpr_pg):
    run_id1, _ = _submit(dpr_pg)
    run_id2, created2 = _submit(dpr_pg, document_sha256="b" * 64)
    assert created2 is True
    assert run_id1 != run_id2


def test_claim_succeeds_once_on_a_queued_run(dpr_pg):
    run_id, _ = _submit(dpr_pg)
    token = claim(dpr_pg, run_id)
    assert token is not None
    row = get_run(dpr_pg, run_id)
    assert row["status"] == "RUNNING"
    assert row["attempts"] == 1


def test_claim_refuses_a_run_id_that_does_not_exist(dpr_pg):
    assert claim(dpr_pg, "00000000-0000-0000-0000-000000000000") is None


def test_claim_refuses_a_run_with_an_unexpired_lease(dpr_pg):
    """Two workers must never both claim the same run."""
    run_id, _ = _submit(dpr_pg)
    first = claim(dpr_pg, run_id, lease_seconds=900)
    assert first is not None
    second = claim(dpr_pg, run_id, lease_seconds=900)
    assert second is None


def test_claim_reclaims_a_run_whose_lease_has_expired(dpr_pg):
    run_id, _ = _submit(dpr_pg)
    assert claim(dpr_pg, run_id, lease_seconds=900) is not None
    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    with dpr_pg.begin() as conn:
        conn.execute(
            text("UPDATE dpr_analysis_runs SET lease_until=:expired WHERE run_id=:id"),
            {"expired": expired, "id": run_id},
        )
    second = claim(dpr_pg, run_id, lease_seconds=900)
    assert second is not None
    assert get_run(dpr_pg, run_id)["attempts"] == 2


def test_claim_stops_after_four_attempts(dpr_pg):
    """attempts < 4 in the WHERE clause: a 5th claim must be refused."""
    run_id, _ = _submit(dpr_pg)
    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    for _ in range(4):
        assert claim(dpr_pg, run_id, lease_seconds=900) is not None
        with dpr_pg.begin() as conn:
            conn.execute(
                text(
                    "UPDATE dpr_analysis_runs SET lease_until=:expired WHERE run_id=:id"
                ),
                {"expired": expired, "id": run_id},
            )
    assert claim(dpr_pg, run_id, lease_seconds=900) is None
    assert get_run(dpr_pg, run_id)["attempts"] == 4


def test_renew_lease_extends_it_and_keeps_status_running(dpr_pg):
    run_id, _ = _submit(dpr_pg)
    token = claim(dpr_pg, run_id, lease_seconds=1)
    before = get_run(dpr_pg, run_id)
    assert renew_lease(dpr_pg, run_id, token, lease_seconds=900) is True
    with dpr_pg.connect() as conn:
        lease_until = conn.execute(
            text("SELECT lease_until FROM dpr_analysis_runs WHERE run_id=:id"),
            {"id": run_id},
        ).scalar_one()
    assert lease_until > datetime.now(timezone.utc) + timedelta(seconds=800)
    assert get_run(dpr_pg, run_id)["status"] == "RUNNING"
    assert before["status"] == "RUNNING"  # unchanged by the renewal


def test_renew_lease_refuses_a_wrong_token(dpr_pg):
    run_id, _ = _submit(dpr_pg)
    claim(dpr_pg, run_id, lease_seconds=1)
    wrong_but_valid_uuid = "00000000-0000-0000-0000-000000000000"
    assert renew_lease(dpr_pg, run_id, wrong_but_valid_uuid, lease_seconds=900) is False


def test_renew_lease_refuses_a_malformed_token(dpr_pg):
    run_id, _ = _submit(dpr_pg)
    claim(dpr_pg, run_id, lease_seconds=1)
    assert renew_lease(dpr_pg, run_id, "not-a-uuid", lease_seconds=900) is False


def test_renew_lease_does_nothing_once_the_run_is_finished(dpr_pg):
    run_id, _ = _submit(dpr_pg)
    token = claim(dpr_pg, run_id)
    assert finish(dpr_pg, run_id, token, success=True) is True
    assert renew_lease(dpr_pg, run_id, token, lease_seconds=900) is False


def test_finish_requires_the_matching_lease_token(dpr_pg):
    """A worker that lost its lease must not be able to complete the run."""
    run_id, _ = _submit(dpr_pg)
    claim(dpr_pg, run_id)
    wrong_but_valid_uuid = "00000000-0000-0000-0000-000000000000"
    assert finish(dpr_pg, run_id, wrong_but_valid_uuid, success=True) is False
    assert get_run(dpr_pg, run_id)["status"] == "RUNNING"


def test_finish_refuses_a_malformed_token_instead_of_raising(dpr_pg):
    """lease_token is a UUID column; a garbled token must not crash finish()."""
    run_id, _ = _submit(dpr_pg)
    claim(dpr_pg, run_id)
    assert finish(dpr_pg, run_id, "not-a-uuid-at-all", success=True) is False
    assert get_run(dpr_pg, run_id)["status"] == "RUNNING"


def test_finish_marks_success_and_clears_the_lease(dpr_pg):
    run_id, _ = _submit(dpr_pg)
    token = claim(dpr_pg, run_id)
    assert (
        finish(dpr_pg, run_id, token, success=True, artifact_ref="report-123") is True
    )
    row = get_run(dpr_pg, run_id)
    assert row["status"] == "COMPLETED"
    assert row["artifact_ref"] == "report-123"
    # The lease is gone, so no one -- not even the same token -- can finish it again.
    assert finish(dpr_pg, run_id, token, success=True) is False


def test_finish_marks_failure_with_an_error_code(dpr_pg):
    run_id, _ = _submit(dpr_pg)
    token = claim(dpr_pg, run_id)
    assert finish(dpr_pg, run_id, token, success=False, error_code="GeminiAgentError")
    row = get_run(dpr_pg, run_id)
    assert row["status"] == "FAILED"
    assert row["error_code"] == "GeminiAgentError"


def test_fail_stuck_run_terminal_fails_a_queued_run_with_no_lease(dpr_pg):
    """The case fail_stuck_run() exists for: a run that was never claimed
    (e.g. a config error raised while building run_claimed_analysis()'s own
    arguments, before claim() ever runs) must not stay QUEUED forever."""
    run_id, _ = _submit(dpr_pg)
    assert get_run(dpr_pg, run_id)["status"] == "QUEUED"
    assert fail_stuck_run(dpr_pg, run_id, "AttributeError") is True
    row = get_run(dpr_pg, run_id)
    assert row["status"] == "FAILED"
    assert row["error_code"] == "AttributeError"


def test_fail_stuck_run_also_fails_a_running_run_ignoring_the_lease(dpr_pg):
    run_id, _ = _submit(dpr_pg)
    claim(dpr_pg, run_id)  # leaves it RUNNING, with an active lease
    assert fail_stuck_run(dpr_pg, run_id, "RuntimeError") is True
    row = get_run(dpr_pg, run_id)
    assert row["status"] == "FAILED"
    assert row["error_code"] == "RuntimeError"


def test_fail_stuck_run_is_a_no_op_once_already_terminal(dpr_pg):
    """Safe to call unconditionally after a normal finish() -- it must never
    clobber a result that's already COMPLETED or re-fail an already-FAILED
    run with a different error_code."""
    run_id, _ = _submit(dpr_pg)
    token = claim(dpr_pg, run_id)
    finish(dpr_pg, run_id, token, success=True, artifact_ref="report-123")
    assert fail_stuck_run(dpr_pg, run_id, "SomeLaterError") is False
    row = get_run(dpr_pg, run_id)
    assert row["status"] == "COMPLETED"
    assert row["artifact_ref"] == "report-123"


def test_get_run_returns_none_for_an_unknown_id(dpr_pg):
    assert get_run(dpr_pg, "00000000-0000-0000-0000-000000000000") is None


def _expire(engine, run_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE dpr_analysis_runs SET lease_until=:expired WHERE run_id=:id"),
            {
                "expired": datetime.now(timezone.utc) - timedelta(seconds=1),
                "id": run_id,
            },
        )


def test_list_claimable_finds_queued_and_stale_running_runs(dpr_pg):
    queued, _ = _submit(dpr_pg)
    stale, _ = _submit(dpr_pg, document_sha256="b" * 64)
    claim(dpr_pg, stale)
    _expire(dpr_pg, stale)
    held, _ = _submit(dpr_pg, document_sha256="c" * 64)
    claim(dpr_pg, held)  # fresh lease -- not claimable yet

    claimable = list_claimable(dpr_pg)

    assert set(claimable) == {queued, stale}


def test_list_claimable_excludes_attempts_exhausted_runs(dpr_pg):
    run_id, _ = _submit(dpr_pg)
    for _ in range(4):
        claim(dpr_pg, run_id, lease_seconds=900)
        _expire(dpr_pg, run_id)

    assert list_claimable(dpr_pg) == []


def test_reap_exhausted_marks_stuck_runs_failed(dpr_pg):
    run_id, _ = _submit(dpr_pg)
    for _ in range(4):
        claim(dpr_pg, run_id, lease_seconds=900)
        _expire(dpr_pg, run_id)

    reaped = reap_exhausted(dpr_pg)

    assert reaped == [run_id]
    row = get_run(dpr_pg, run_id)
    assert row["status"] == "FAILED"
    assert row["error_code"] == "ATTEMPTS_EXHAUSTED"


def test_reap_exhausted_leaves_runs_with_attempts_left_alone(dpr_pg):
    run_id, _ = _submit(dpr_pg)
    claim(dpr_pg, run_id, lease_seconds=900)
    _expire(dpr_pg, run_id)  # stale, but only 1 attempt so far

    assert reap_exhausted(dpr_pg) == []
    assert get_run(dpr_pg, run_id)["status"] == "RUNNING"
