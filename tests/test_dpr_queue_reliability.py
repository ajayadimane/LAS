"""Job-queue reliability (review 2026-11-25): a worker that loses the claim
race, a lost or failing lease heartbeat, a stop on deploy, and re-uploading
the same document. Real Postgres (tests/conftest.py's dpr_pg)."""

import asyncio
import hashlib
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import text

from app.core.config import Settings
from app.services.documents import StoredFile
from app.services.dpr_agentic import worker as worker_mod
from app.services.dpr_agentic.document_queue import queue_analysis
from app.services.dpr_agentic.financial_schema import ProjectFinancials
from app.services.dpr_agentic.jobs import (
    claim,
    finish,
    get_run,
    list_claimable,
    record_token_usage,
    submit,
)
from app.services.dpr_agentic.schemas import AgentOutput, AnalysisInput
from app.services.dpr_agentic.upload_staging import (
    load_staged_upload,
    purge_terminal_staging,
    stage_upload,
)
from app.services.dpr_agentic.worker import LeaseLostError, run_claimed_analysis

_WORKER_SPEC = importlib.util.spec_from_file_location(
    "dpr_worker_queue_reliability_test",
    Path(__file__).resolve().parents[1] / "scripts" / "dpr_worker.py",
)
dpr_worker = importlib.util.module_from_spec(_WORKER_SPEC)
_WORKER_SPEC.loader.exec_module(dpr_worker)

KEY = Fernet.generate_key()
DOC = b"%PDF-1.4 a DPR"
SHA = hashlib.sha256(DOC).hexdigest()
REF = "wallet-person-custom-field:9000000013:DPR_ANALYSIS"


class FakeGemini:
    async def generate(self, prompt, context):
        return AgentOutput(summary="checked")

    async def extract(self, prompt, context):
        return ProjectFinancials()


def _queued_and_staged(engine) -> str:
    run_id, _ = submit(
        engine, service_request_id="SR-1", document_sha256=SHA, source_reference=REF
    )
    stage_upload(
        engine,
        run_id,
        StoredFile(name="dpr.pdf", content_type="application/pdf", data=DOC),
        KEY,
    )
    return run_id


def _payload(run_id: str) -> AnalysisInput:
    return AnalysisInput(
        service_request_id="SR-1",
        wallet_document_id=run_id,
        document_sha256=SHA,
        pages=[{"page": 1, "text": "Project cost is 80 lakh."}],
    )


def _settings(**overrides) -> Settings:
    values = dict(
        _env_file=None,
        dpr_agentic_vision_enabled=False,
        dpr_agentic_vision_max_pages=20,
        dpr_agentic_liteparse_page_min_chars=50,
        cam_engine_enabled=False,
        dpr_cam_pdf_render_timeout_seconds=20.0,
        dpr_staging_encryption_key=SecretStr(KEY.decode()),
    )
    values.update(overrides)
    return Settings(**values)


# --- a worker that loses the claim race -------------------------------------


def test_a_worker_that_loses_the_claim_leaves_the_winners_run_alone(dpr_pg):
    """Two workers listed the same run; the other one claimed it first. The
    loser must not delete the staged upload the winner is about to read,
    nor write its own (zero) usage over the run."""
    run_id = _queued_and_staged(dpr_pg)
    assert claim(dpr_pg, run_id, lease_seconds=900) is not None  # the winner
    record_token_usage(
        dpr_pg,
        run_id,
        gemini_calls=7,
        prompt_tokens=700,
        completion_tokens=70,
        total_tokens=770,
    )

    asyncio.run(dpr_worker.process_one(dpr_pg, KEY, FakeGemini(), run_id, _settings()))

    assert load_staged_upload(dpr_pg, run_id, KEY) is not None
    row = get_run(dpr_pg, run_id)
    assert row["status"] == "RUNNING"
    assert row["gemini_calls"] == 7


def test_a_completed_run_still_cleans_up_its_staged_upload(dpr_pg):
    run_id = _queued_and_staged(dpr_pg)

    async def fake_run_claimed(engine, rid, *args, on_claimed=None, **kwargs):
        token = claim(engine, rid)
        on_claimed()
        return finish(engine, rid, token, success=True, artifact_ref="report-1")

    with patch.object(dpr_worker, "run_claimed_analysis", fake_run_claimed):
        asyncio.run(
            dpr_worker.process_one(dpr_pg, KEY, FakeGemini(), run_id, _settings())
        )

    assert get_run(dpr_pg, run_id)["status"] == "COMPLETED"
    assert load_staged_upload(dpr_pg, run_id, KEY) is None


def test_the_sweep_removes_staged_bytes_left_behind_by_finished_runs(dpr_pg):
    done = _queued_and_staged(dpr_pg)
    token = claim(dpr_pg, done)
    finish(dpr_pg, done, token, success=False, error_code="Boom")
    waiting, _ = submit(
        dpr_pg, service_request_id="SR-2", document_sha256=SHA, source_reference=REF
    )
    stage_upload(
        dpr_pg,
        waiting,
        StoredFile(name="dpr.pdf", content_type="application/pdf", data=DOC),
        KEY,
    )

    assert purge_terminal_staging(dpr_pg) == 1
    assert load_staged_upload(dpr_pg, done, KEY) is None
    assert load_staged_upload(dpr_pg, waiting, KEY) is not None


# --- heartbeat ----------------------------------------------------------------


def test_one_failed_lease_renewal_neither_kills_the_heartbeat_nor_the_result(
    dpr_pg,
):
    run_id = _queued_and_staged(dpr_pg)
    real_renew = worker_mod.renew_lease
    calls = {"n": 0}

    def flaky_renew(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database went away for a moment")
        return real_renew(*args, **kwargs)

    async def slow_load(rid):
        await asyncio.sleep(0.8)
        return _payload(rid)

    async def store(rid, result):
        return f"report-ref:{rid}"

    with patch.object(worker_mod, "renew_lease", flaky_renew):
        ok = asyncio.run(
            run_claimed_analysis(
                dpr_pg,
                run_id,
                FakeGemini(),
                slow_load,
                store,
                lease_seconds=60,
                heartbeat_interval=0.2,
            )
        )

    assert ok is True
    assert calls["n"] >= 2  # kept beating after the error
    assert get_run(dpr_pg, run_id)["status"] == "COMPLETED"


def test_a_lost_lease_stops_the_work_and_never_finishes_the_run(dpr_pg):
    run_id = _queued_and_staged(dpr_pg)
    stored = []

    async def slow_load(rid):
        await asyncio.sleep(5)
        return _payload(rid)

    async def store(rid, result):
        stored.append(rid)
        return "never"

    with patch.object(worker_mod, "renew_lease", lambda *a, **k: False):
        with pytest.raises(LeaseLostError):
            asyncio.run(
                run_claimed_analysis(
                    dpr_pg,
                    run_id,
                    FakeGemini(),
                    slow_load,
                    store,
                    lease_seconds=60,
                    heartbeat_interval=0.1,
                )
            )

    assert stored == []
    # Left to whoever holds it now: not FAILED by this worker.
    assert get_run(dpr_pg, run_id)["status"] == "RUNNING"


# --- stopping on deploy -----------------------------------------------------


def test_a_stop_mid_run_hands_it_back_without_spending_an_attempt(dpr_pg):
    run_id = _queued_and_staged(dpr_pg)
    started = asyncio.Event()

    async def slow_load(rid):
        started.set()
        await asyncio.sleep(30)
        return _payload(rid)

    async def store(rid, result):
        return "never"

    async def scenario():
        task = asyncio.create_task(
            run_claimed_analysis(
                dpr_pg, run_id, FakeGemini(), slow_load, store, lease_seconds=1800
            )
        )
        await started.wait()
        task.cancel()  # what the SIGTERM handler does to the main task
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    row = get_run(dpr_pg, run_id)
    assert row["status"] == "QUEUED"
    assert row["attempts"] == 0
    assert list_claimable(dpr_pg) == [run_id]  # picked up again at once


def test_process_one_stopped_mid_run_records_usage_and_keeps_the_upload(dpr_pg):
    run_id = _queued_and_staged(dpr_pg)

    async def fake_run_claimed(engine, rid, *args, on_claimed=None, **kwargs):
        claim(engine, rid)
        on_claimed()
        dpr_worker.CURRENT_RUN_USAGE.get().append(
            {"role": "finance", "prompt_tokens": 100, "completion_tokens": 10}
        )
        raise asyncio.CancelledError

    with patch.object(dpr_worker, "run_claimed_analysis", fake_run_claimed):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(
                dpr_worker.process_one(dpr_pg, KEY, FakeGemini(), run_id, _settings())
            )

    assert get_run(dpr_pg, run_id)["gemini_calls"] == 1
    assert load_staged_upload(dpr_pg, run_id, KEY) is not None


# --- re-uploading the same document -----------------------------------------


def _queue(engine, settings=None):
    return queue_analysis(
        engine,
        settings or _settings(),
        person_id="9000000013",
        code="DPR_ANALYSIS",
        file=StoredFile(name="dpr.pdf", content_type="application/pdf", data=DOC),
        project_context={},
        wallet_status="STORED",
    )


def _staging_rows(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(text("SELECT COUNT(*) FROM dpr_upload_staging")).scalar()


def test_re_uploading_an_analysed_document_stages_nothing(dpr_pg):
    status, run_id = _queue(dpr_pg)
    assert status == "QUEUED"
    token = claim(dpr_pg, run_id)
    finish(dpr_pg, run_id, token, success=True, artifact_ref="report-1")
    dpr_worker.delete_staged_upload_if_terminal(dpr_pg, run_id)

    again, same_run = _queue(dpr_pg)

    assert (again, same_run) == ("ALREADY_ANALYSED", run_id)
    assert _staging_rows(dpr_pg) == 0  # no bytes left behind for a finished run


def test_re_uploading_a_failed_document_queues_it_again(dpr_pg):
    status, run_id = _queue(dpr_pg)
    token = claim(dpr_pg, run_id)
    finish(dpr_pg, run_id, token, success=False, error_code="GeminiQuotaError")
    dpr_worker.delete_staged_upload_if_terminal(dpr_pg, run_id)

    again, same_run = _queue(dpr_pg)

    assert (again, same_run) == ("QUEUED", run_id)
    row = get_run(dpr_pg, run_id)
    assert row["status"] == "QUEUED" and row["attempts"] == 0
    assert list_claimable(dpr_pg) == [run_id]


def test_the_daily_cap_still_applies(dpr_pg):
    settings = _settings(dpr_agentic_daily_submission_limit=1)
    assert _queue(dpr_pg, settings)[0] == "QUEUED"
    other = StoredFile(name="b.pdf", content_type="application/pdf", data=b"other")
    status, run_id = queue_analysis(
        dpr_pg,
        settings,
        person_id="9000000013",
        code="DPR_ANALYSIS",
        file=other,
        project_context={},
        wallet_status="STORED",
    )
    assert (status, run_id) == ("RATE_LIMITED", None)
