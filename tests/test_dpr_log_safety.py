"""Regression guard: no document page text or raw LLM response content may
ever reach the logs, on either the sequential orchestrator path or the
worker path. Confirmed clean by code inspection (see INCREMENT11.md); these
tests pin that so a future change can't quietly regress it.

A document's content and whatever an LLM echoes back from it are both
untrusted and potentially sensitive (financial figures, PII); logs are
commonly shipped to less-trusted aggregation systems than the app itself.
"""

import asyncio
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import respx
from cryptography.fernet import Fernet

from app.core.config import Settings
from app.services.documents import StoredFile
from app.services.dpr_agentic.financial_schema import ProjectFinancials
from app.services.dpr_agentic.gemini import (
    CURRENT_RUN_ID,
    CURRENT_RUN_USAGE,
    GeminiAgentClient,
)
from app.services.dpr_agentic.jobs import get_run, submit
from app.services.dpr_agentic.orchestrator import analyze
from app.services.dpr_agentic.schemas import AgentOutput, AnalysisInput
from app.services.dpr_agentic.upload_staging import stage_upload

_WORKER_SPEC = importlib.util.spec_from_file_location(
    "dpr_worker_log_safety_test",
    Path(__file__).resolve().parents[1] / "scripts" / "dpr_worker.py",
)
dpr_worker = importlib.util.module_from_spec(_WORKER_SPEC)
_WORKER_SPEC.loader.exec_module(dpr_worker)

CANARY = "CANARY-SECRET-FIGURE-should-never-appear-in-logs-9f3a7b"
KEY = Fernet.generate_key()


def _worker_settings():
    """A real Settings(), not a bare SimpleNamespace missing attributes like
    cam_engine_enabled -- process_one() reads that as a keyword argument to
    run_claimed_analysis() before claim() ever runs, so a missing attribute
    raised an AttributeError that left the run QUEUED forever (never
    claimed, so finish() was never reached either) instead of exercising
    what each of these tests actually claims to test. See PROGRESS.md
    milestone 7 prep for the fix (jobs.fail_stuck_run()) this gap drove."""
    return Settings(
        _env_file=None,
        dpr_agentic_vision_enabled=False,
        dpr_agentic_vision_max_pages=20,
        dpr_agentic_liteparse_page_min_chars=50,
        cam_engine_enabled=False,
        dpr_cam_pdf_render_timeout_seconds=20.0,
    )


def _all_log_text(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records)


def test_gemini_client_logs_only_token_counts_never_content(caplog):
    """The real GeminiAgentClient, not a fake -- confirms its own new
    per-call usage logging never includes prompt/response content."""
    caplog.set_level(logging.INFO)
    fake_response = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps(
                                {
                                    "summary": CANARY,
                                    "findings": [],
                                    "unresolved_questions": [],
                                }
                            )
                        }
                    ]
                }
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 123,
            "candidatesTokenCount": 45,
            "totalTokenCount": 168,
        },
    }

    async def run():
        with respx.mock(assert_all_called=True) as router:
            router.post(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-2.5-flash:generateContent"
            ).mock(return_value=httpx.Response(200, json=fake_response))
            async with httpx.AsyncClient() as http:
                client = GeminiAgentClient(http, "fake-key", "gemini-2.5-flash")
                return await client.generate(
                    "system prompt", {"dpr": {"pages": [{"page": 1, "text": CANARY}]}}
                )

    result = asyncio.run(run())

    assert result.summary == CANARY  # sanity: the call actually went through
    log_text = _all_log_text(caplog)
    assert "dpr_gemini_call" in log_text
    assert "123" in log_text
    assert "45" in log_text
    assert "168" in log_text
    assert CANARY not in log_text
    assert "fake-key" not in log_text


def test_gemini_client_logs_the_current_run_id_when_set(caplog):
    """CURRENT_RUN_ID is set once per analysis by scripts/dpr_worker.py's
    process_one(); this confirms the client actually reads it."""
    caplog.set_level(logging.INFO)
    fake_response = {
        "candidates": [
            {"content": {"parts": [{"text": json.dumps({"text": "page text"})}]}}
        ],
        "usageMetadata": {
            "promptTokenCount": 1,
            "candidatesTokenCount": 1,
            "totalTokenCount": 2,
        },
    }

    async def run():
        token = CURRENT_RUN_ID.set("run-abc-123")
        try:
            with respx.mock(assert_all_called=True) as router:
                router.post(
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    "gemini-2.5-flash:generateContent"
                ).mock(return_value=httpx.Response(200, json=fake_response))
                async with httpx.AsyncClient() as http:
                    client = GeminiAgentClient(http, "fake-key", "gemini-2.5-flash")
                    await client.ocr_page(b"fake-image-bytes")
        finally:
            CURRENT_RUN_ID.reset(token)

    asyncio.run(run())

    assert "run_id=run-abc-123" in _all_log_text(caplog)


def test_gemini_client_appends_usage_to_the_current_run_usage_list_when_set():
    """CURRENT_RUN_USAGE is the aggregation point process_one() reads at the
    end of a run to log one end-to-end token total; this confirms the client
    actually appends to it (and leaves it alone when nothing is listening,
    e.g. the ad hoc scripts/tests that never set it)."""
    fake_response = {
        "candidates": [{"content": {"parts": [{"text": json.dumps({"text": "x"})}]}}],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 20,
            "totalTokenCount": 30,
        },
    }

    async def run(usage_log):
        run_token = CURRENT_RUN_USAGE.set(usage_log)
        try:
            with respx.mock(assert_all_called=True) as router:
                router.post(
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    "gemini-2.5-flash:generateContent"
                ).mock(return_value=httpx.Response(200, json=fake_response))
                async with httpx.AsyncClient() as http:
                    client = GeminiAgentClient(http, "fake-key", "gemini-2.5-flash")
                    await client.ocr_page(b"fake-image-bytes")
        finally:
            CURRENT_RUN_USAGE.reset(run_token)

    # Nothing listening (the default outside a run): must not raise or
    # fabricate a list.
    asyncio.run(run(None))
    assert CURRENT_RUN_USAGE.get() is None

    usage_log: list[dict] = []
    asyncio.run(run(usage_log))
    assert usage_log == [
        {
            "role": "unknown",
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
        }
    ]
    assert CURRENT_RUN_USAGE.get() is None  # reset after


def test_orchestrator_never_logs_page_text_or_llm_output(caplog):
    caplog.set_level(logging.DEBUG)

    class CanaryLLM:
        async def generate(self, prompt, context):
            return AgentOutput(summary=CANARY, unresolved_questions=[CANARY])

        async def extract(self, prompt, context):
            return ProjectFinancials()

    payload = AnalysisInput(
        service_request_id="SR-1",
        wallet_document_id="W-1",
        document_sha256=hashlib.sha256(b"sample").hexdigest(),
        pages=[{"page": 1, "text": f"Document body containing {CANARY}"}],
    )

    asyncio.run(analyze(payload, CanaryLLM()))

    assert CANARY not in _all_log_text(caplog)


def test_worker_never_logs_page_text_or_llm_output(dpr_pg, caplog):
    caplog.set_level(logging.DEBUG)

    class CanaryGemini:
        async def generate(self, prompt, context):
            return AgentOutput(summary=CANARY)

        async def extract(self, prompt, context):
            return ProjectFinancials()

        async def ocr_page(self, image_bytes, mime_type="image/png"):
            return CANARY

    run_id, _ = submit(
        dpr_pg,
        service_request_id="SR-1",
        document_sha256=hashlib.sha256(b"doc").hexdigest(),
        source_reference="wallet-person-custom-field:9000000013:GEO_CHECK",
    )
    stage_upload(
        dpr_pg,
        run_id,
        StoredFile(name="report.pdf", content_type="application/pdf", data=b"dummy"),
        KEY,
    )
    parsed = SimpleNamespace(
        sha256=hashlib.sha256(b"doc").hexdigest(),
        mime="application/pdf",
        doc_class="DPR",
        confidence=0.99,
        needs_vision=False,
        pages=[{"page": 1, "text": f"Document body containing {CANARY}"}],
    )

    with patch.object(dpr_worker, "inspect_document", return_value=parsed):
        settings = _worker_settings()
        asyncio.run(
            dpr_worker.process_one(dpr_pg, KEY, CanaryGemini(), run_id, settings)
        )

    assert CANARY not in _all_log_text(caplog)


def test_process_one_sets_current_run_id_for_the_whole_call_chain(dpr_pg):
    """Proves the contextvar actually propagates through process_one ->
    run_claimed_analysis -> analyze_parallel -> the LangGraph nodes ->
    llm.generate(), not just that the variable exists."""
    seen_run_ids: list[str | None] = []

    class RecordingGemini:
        async def generate(self, prompt, context):
            seen_run_ids.append(CURRENT_RUN_ID.get())
            return AgentOutput(summary="checked")

        async def extract(self, prompt, context):
            seen_run_ids.append(CURRENT_RUN_ID.get())
            return ProjectFinancials()

    run_id, _ = submit(
        dpr_pg,
        service_request_id="SR-1",
        document_sha256=hashlib.sha256(b"doc3").hexdigest(),
        source_reference="wallet-person-custom-field:9000000013:GEO_CHECK",
    )
    stage_upload(
        dpr_pg,
        run_id,
        StoredFile(name="report.pdf", content_type="application/pdf", data=b"dummy"),
        KEY,
    )
    parsed = SimpleNamespace(
        sha256=hashlib.sha256(b"doc3").hexdigest(),
        mime="application/pdf",
        doc_class="DPR",
        confidence=0.99,
        needs_vision=False,
        pages=[{"page": 1, "text": "Detailed project report cost 100"}],
    )

    assert CURRENT_RUN_ID.get() is None  # nothing set outside a run
    with patch.object(dpr_worker, "inspect_document", return_value=parsed):
        settings = _worker_settings()
        asyncio.run(
            dpr_worker.process_one(dpr_pg, KEY, RecordingGemini(), run_id, settings)
        )

    # extract() once, generate() once per role (10 roles) -- every single
    # call, across every LangGraph node, saw the same run_id.
    assert len(seen_run_ids) == 11
    assert all(seen == run_id for seen in seen_run_ids)
    assert CURRENT_RUN_ID.get() is None  # reset after the run finishes


def test_process_one_logs_a_token_usage_summary_and_resets_the_context(dpr_pg, caplog):
    """The end-to-end rollup process_one() is responsible for, independent of
    the per-call dpr_gemini_call lines GeminiAgentClient emits itself. This
    fake Gemini bypasses GeminiAgentClient entirely (no HTTP, no usage
    metadata), so the summary is expected to show zero -- the point here is
    that the line is always emitted and the context var is always reset,
    not the arithmetic (covered by the gemini.py-level test above)."""
    caplog.set_level(logging.INFO)

    class CanaryGemini:
        async def generate(self, prompt, context):
            return AgentOutput(summary="checked")

        async def extract(self, prompt, context):
            return ProjectFinancials()

    run_id, _ = submit(
        dpr_pg,
        service_request_id="SR-1",
        document_sha256=hashlib.sha256(b"doc4").hexdigest(),
        source_reference="wallet-person-custom-field:9000000013:GEO_CHECK",
    )
    stage_upload(
        dpr_pg,
        run_id,
        StoredFile(name="report.pdf", content_type="application/pdf", data=b"dummy"),
        KEY,
    )
    parsed = SimpleNamespace(
        sha256=hashlib.sha256(b"doc4").hexdigest(),
        mime="application/pdf",
        doc_class="DPR",
        confidence=0.99,
        needs_vision=False,
        pages=[{"page": 1, "text": "Detailed project report cost 100"}],
    )

    assert CURRENT_RUN_USAGE.get() is None
    with patch.object(dpr_worker, "inspect_document", return_value=parsed):
        settings = _worker_settings()
        asyncio.run(
            dpr_worker.process_one(dpr_pg, KEY, CanaryGemini(), run_id, settings)
        )

    log_text = _all_log_text(caplog)
    assert f"dpr_run_token_usage run_id={run_id}" in log_text
    assert "total_tokens=0" in log_text
    assert CURRENT_RUN_USAGE.get() is None  # reset after the run finishes

    # Migration 008: persisted on dpr_analysis_runs, not just logged -- a
    # fake Gemini that bypasses GeminiAgentClient still exercises the
    # persistence path itself (gemini_calls=0 is a real, written value, not
    # the NULL a pre-migration or never-run row would show).
    row = get_run(dpr_pg, run_id)
    assert row["gemini_calls"] == 0
    assert row["prompt_tokens"] == 0
    assert row["completion_tokens"] == 0
    assert row["total_tokens"] == 0


def test_worker_failure_logging_never_includes_exception_message_text(dpr_pg, caplog):
    """Only the exception TYPE name may be logged, never its message/args --
    an exception message could itself echo back document content."""
    caplog.set_level(logging.DEBUG)

    class RaisingGemini:
        async def generate(self, prompt, context):
            raise RuntimeError(CANARY)

        async def extract(self, prompt, context):
            return ProjectFinancials()

    run_id, _ = submit(
        dpr_pg,
        service_request_id="SR-1",
        document_sha256=hashlib.sha256(b"doc2").hexdigest(),
        source_reference="wallet-person-custom-field:9000000013:GEO_CHECK",
    )
    stage_upload(
        dpr_pg,
        run_id,
        StoredFile(name="report.pdf", content_type="application/pdf", data=b"dummy"),
        KEY,
    )
    parsed = SimpleNamespace(
        sha256=hashlib.sha256(b"doc2").hexdigest(),
        mime="application/pdf",
        doc_class="DPR",
        confidence=0.99,
        needs_vision=False,
        pages=[{"page": 1, "text": "Detailed project report cost 100"}],
    )

    with patch.object(dpr_worker, "inspect_document", return_value=parsed):
        settings = _worker_settings()
        asyncio.run(
            dpr_worker.process_one(dpr_pg, KEY, RaisingGemini(), run_id, settings)
        )

    assert CANARY not in _all_log_text(caplog)


def test_a_config_error_before_claim_still_terminal_fails_the_run(dpr_pg, caplog):
    """Regression test for the exact bug this file's bare-SimpleNamespace
    settings used to hide: `settings.cam_engine_enabled` is read as a
    keyword argument to run_claimed_analysis() -- before claim() ever runs
    -- so a `settings` object missing that attribute raised AttributeError
    pre-claim. process_one()'s except block used to only log a warning,
    leaving the run QUEUED forever (never claimed, so finish() was never
    reached) rather than ending up FAILED with a reason recorded -- every
    poll cycle would have retried it identically, forever. Now
    jobs.fail_stuck_run() is the backstop; this proves the run actually
    ends up FAILED, with an error_code, not stuck."""
    caplog.set_level(logging.WARNING)

    class UnreachableGemini:
        async def generate(self, prompt, context):
            raise AssertionError(
                "must not be reached: the config error is before claim()"
            )

        async def extract(self, prompt, context):
            raise AssertionError(
                "must not be reached: the config error is before claim()"
            )

    run_id, _ = submit(
        dpr_pg,
        service_request_id="SR-1",
        document_sha256=hashlib.sha256(b"doc-config-error").hexdigest(),
        source_reference="wallet-person-custom-field:9000000013:GEO_CHECK",
    )
    stage_upload(
        dpr_pg,
        run_id,
        StoredFile(name="report.pdf", content_type="application/pdf", data=b"dummy"),
        KEY,
    )

    # A settings object missing cam_engine_enabled -- the exact shape that
    # used to hang every one of this file's worker-level tests.
    broken_settings = SimpleNamespace(
        dpr_agentic_vision_enabled=False,
        dpr_agentic_vision_max_pages=20,
        dpr_agentic_liteparse_page_min_chars=50,
    )

    asyncio.run(
        dpr_worker.process_one(
            dpr_pg, KEY, UnreachableGemini(), run_id, broken_settings
        )
    )

    run = get_run(dpr_pg, run_id)
    assert run["status"] == "FAILED"
    assert run["error_code"] == "AttributeError"
    assert "dpr_run_failed" in _all_log_text(caplog)
    assert "error=AttributeError" in _all_log_text(caplog)
