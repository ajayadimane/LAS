"""Integration tests for app/services/dpr_agentic/branch_status.py.

See tests/conftest.py for the Postgres fixtures these need.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.dpr_agentic.branch_status import (
    get_branch_status,
    init_branches,
    mark_report_completed,
    mark_wallet_retry,
    mark_wallet_stored,
)
from app.services.dpr_agentic.jobs import submit


def _run(engine, **overrides):
    defaults = dict(
        service_request_id="SR-1",
        document_sha256="c" * 64,
        source_reference="wallet-person-custom-field:9000000013:DPR_ANALYSIS",
    )
    defaults.update(overrides)
    run_id, _ = submit(engine, **defaults)
    return run_id


def test_init_branches_sets_the_documented_defaults(dpr_pg):
    run_id = _run(dpr_pg)
    init_branches(dpr_pg, run_id)
    row = get_branch_status(dpr_pg, run_id)
    assert row["wallet_storage_status"] == "PENDING"
    assert row["report_status"] == "QUEUED"
    assert row["document_retention_status"] == "UNKNOWN"


def test_init_branches_is_idempotent(dpr_pg):
    run_id = _run(dpr_pg)
    init_branches(dpr_pg, run_id)
    init_branches(dpr_pg, run_id)  # ON CONFLICT DO NOTHING -- must not raise
    assert get_branch_status(dpr_pg, run_id) is not None


def test_mark_wallet_stored_rejects_a_blank_reference(dpr_pg):
    run_id = _run(dpr_pg)
    init_branches(dpr_pg, run_id)
    with pytest.raises(ValueError):
        mark_wallet_stored(dpr_pg, run_id, "   ")


def test_mark_wallet_stored_records_the_reference(dpr_pg):
    run_id = _run(dpr_pg)
    init_branches(dpr_pg, run_id)
    assert mark_wallet_stored(dpr_pg, run_id, "wallet-doc-9000000013") is True
    row = get_branch_status(dpr_pg, run_id)
    assert row["wallet_storage_status"] == "STORED"
    assert row["wallet_reference"] == "wallet-doc-9000000013"


def test_mark_wallet_stored_on_an_unknown_run_reports_no_update(dpr_pg):
    assert (
        mark_wallet_stored(dpr_pg, "00000000-0000-0000-0000-000000000000", "x") is False
    )


def test_a_stale_retry_never_overwrites_a_successful_store(dpr_pg):
    """The whole point of the WHERE clause in mark_wallet_retry."""
    run_id = _run(dpr_pg)
    init_branches(dpr_pg, run_id)
    assert mark_wallet_stored(dpr_pg, run_id, "wallet-doc-1") is True
    retry_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    assert mark_wallet_retry(dpr_pg, run_id, "TIMEOUT", retry_at) is False
    row = get_branch_status(dpr_pg, run_id)
    assert row["wallet_storage_status"] == "STORED"
    assert row["wallet_reference"] == "wallet-doc-1"


def test_mark_wallet_retry_records_the_error_before_any_store(dpr_pg):
    run_id = _run(dpr_pg)
    init_branches(dpr_pg, run_id)
    retry_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    assert mark_wallet_retry(dpr_pg, run_id, "CONNECTION_RESET", retry_at) is True
    row = get_branch_status(dpr_pg, run_id)
    assert row["wallet_storage_status"] == "RETRY"
    assert row["wallet_last_error_code"] == "CONNECTION_RESET"


def test_mark_report_completed_rejects_a_blank_reference(dpr_pg):
    run_id = _run(dpr_pg)
    init_branches(dpr_pg, run_id)
    with pytest.raises(ValueError):
        mark_report_completed(dpr_pg, run_id, "")


def test_mark_report_completed_records_the_artifact(dpr_pg):
    run_id = _run(dpr_pg)
    init_branches(dpr_pg, run_id)
    assert mark_report_completed(dpr_pg, run_id, "report-ref-42") is True
    row = get_branch_status(dpr_pg, run_id)
    assert row["report_status"] == "COMPLETED"
    assert row["report_artifact_ref"] == "report-ref-42"


def test_get_branch_status_returns_none_when_never_initialised(dpr_pg):
    run_id = _run(dpr_pg)
    assert get_branch_status(dpr_pg, run_id) is None
