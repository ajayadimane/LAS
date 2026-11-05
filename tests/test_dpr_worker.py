"""Integration tests for app/services/dpr_agentic/worker.py.

Exercises the real claim/finish lease cycle (Postgres) together with the real
LangGraph orchestration (fake LLM, no network). See tests/conftest.py for the
Postgres fixtures.
"""

import asyncio
import hashlib

import pytest

from app.services.dpr_agentic.financial_schema import ProjectFinancials
from app.services.dpr_agentic.jobs import claim, get_run, submit
from app.services.dpr_agentic.schemas import AgentOutput, AnalysisInput
from app.services.dpr_agentic.worker import run_claimed_analysis


class FakeGemini:
    async def generate(self, prompt, context):
        return AgentOutput(summary="checked")

    async def extract(self, prompt, context):
        return ProjectFinancials()


def _submit(engine, **overrides):
    defaults = dict(
        service_request_id="SR-1",
        document_sha256=hashlib.sha256(b"sample").hexdigest(),
        source_reference="wallet-person-custom-field:9000000013:DPR_ANALYSIS",
    )
    defaults.update(overrides)
    run_id, _ = submit(engine, **defaults)
    return run_id


def _payload(run_id: str) -> AnalysisInput:
    return AnalysisInput(
        service_request_id="SR-1",
        wallet_document_id=run_id,
        document_sha256=hashlib.sha256(b"sample").hexdigest(),
        pages=[{"page": 1, "text": "Project cost is 80 lakh."}],
    )


def test_run_claimed_analysis_completes_and_persists_the_artifact(dpr_pg):
    run_id = _submit(dpr_pg)
    stored = {}

    async def load_input(rid):
        return _payload(rid)

    async def store_result(rid, result):
        stored["result"] = result
        return f"report-ref:{rid}"

    ok = asyncio.run(
        run_claimed_analysis(dpr_pg, run_id, FakeGemini(), load_input, store_result)
    )
    assert ok is True
    row = get_run(dpr_pg, run_id)
    assert row["status"] == "COMPLETED"
    assert row["artifact_ref"] == f"report-ref:{run_id}"
    assert stored["result"].status == "completed"


def test_run_claimed_analysis_returns_false_when_the_run_is_already_claimed(dpr_pg):
    run_id = _submit(dpr_pg)
    claim(dpr_pg, run_id)  # another worker already holds the lease
    called = []

    async def load_input(rid):
        called.append(rid)
        raise AssertionError("must not load input for a run it never claimed")

    async def store_result(rid, result):
        raise AssertionError("must not store a result for a run it never claimed")

    ok = asyncio.run(
        run_claimed_analysis(dpr_pg, run_id, FakeGemini(), load_input, store_result)
    )
    assert ok is False
    assert called == []


def test_run_claimed_analysis_marks_failure_and_reraises_on_load_error(dpr_pg):
    run_id = _submit(dpr_pg)

    async def load_input(rid):
        raise RuntimeError("Wallet document fetch failed")

    async def store_result(rid, result):
        raise AssertionError("must not be called")

    with pytest.raises(RuntimeError):
        asyncio.run(
            run_claimed_analysis(dpr_pg, run_id, FakeGemini(), load_input, store_result)
        )
    row = get_run(dpr_pg, run_id)
    assert row["status"] == "FAILED"
    assert row["error_code"] == "RuntimeError"


def test_heartbeat_renews_the_lease_so_it_outlives_a_slow_analysis(dpr_pg):
    """Without the heartbeat this would be reclaimable after ~1s; the
    analysis takes 2s, so a second worker's claim() attempt partway through
    must still fail -- proof the lease was actively renewed, not just long."""
    run_id = _submit(dpr_pg)

    async def load_input(rid):
        await asyncio.sleep(2.0)
        return _payload(rid)

    async def store_result(rid, result):
        return f"report-ref:{rid}"

    async def scenario():
        task = asyncio.create_task(
            run_claimed_analysis(
                dpr_pg,
                run_id,
                FakeGemini(),
                load_input,
                store_result,
                lease_seconds=1,
                heartbeat_interval=0.2,
            )
        )
        await asyncio.sleep(1.5)  # past the original 1s lease
        concurrent_claim = claim(dpr_pg, run_id, lease_seconds=1)
        return concurrent_claim, await task

    concurrent_claim, ok = asyncio.run(scenario())

    assert concurrent_claim is None  # a second worker could not steal it
    assert ok is True
    assert get_run(dpr_pg, run_id)["status"] == "COMPLETED"


def test_run_claimed_analysis_fails_when_the_artifact_writer_returns_nothing(dpr_pg):
    """store_result returning a falsy ref must not be treated as success."""
    run_id = _submit(dpr_pg)

    async def load_input(rid):
        return _payload(rid)

    async def store_result(rid, result):
        return ""

    with pytest.raises(ValueError):
        asyncio.run(
            run_claimed_analysis(dpr_pg, run_id, FakeGemini(), load_input, store_result)
        )
    row = get_run(dpr_pg, run_id)
    assert row["status"] == "FAILED"
    assert row["error_code"] == "ValueError"
