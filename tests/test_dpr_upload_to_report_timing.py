"""The upload -> report-ready latency the user actually experiences, not
just this worker's own processing time (scripts/dpr_worker.py already logs
that as dpr_run_completed). created_at is set at submit() time -- the same
request that stores the uploaded document
(document_queue.upload_and_queue_analysis) -- so `now - created_at` at
successful completion is the full, real latency, queue wait included.
"""

import asyncio
import hashlib
import importlib.util
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.fernet import Fernet

from app.core.config import Settings
from app.services.documents import StoredFile
from app.services.dpr_agentic.financial_schema import ProjectFinancials
from app.services.dpr_agentic.jobs import get_run, submit
from app.services.dpr_agentic.schemas import AgentOutput
from app.services.dpr_agentic.upload_staging import stage_upload

_WORKER_SPEC = importlib.util.spec_from_file_location(
    "dpr_worker_timing_test",
    Path(__file__).resolve().parents[1] / "scripts" / "dpr_worker.py",
)
dpr_worker = importlib.util.module_from_spec(_WORKER_SPEC)
_WORKER_SPEC.loader.exec_module(dpr_worker)

KEY = Fernet.generate_key()


class _FastGemini:
    async def generate(self, prompt, context):
        return AgentOutput(summary="ok")

    async def extract(self, prompt, context):
        return ProjectFinancials()


def test_a_completed_run_logs_the_full_upload_to_report_latency(dpr_pg, caplog):
    caplog.set_level(logging.INFO)
    run_id, _ = submit(
        dpr_pg,
        service_request_id="SR-1",
        document_sha256=hashlib.sha256(b"timing-doc").hexdigest(),
        source_reference="wallet-person-custom-field:9000000013:GEO_CHECK",
    )
    stage_upload(
        dpr_pg,
        run_id,
        StoredFile(name="report.pdf", content_type="application/pdf", data=b"dummy"),
        KEY,
    )
    parsed = SimpleNamespace(
        sha256=hashlib.sha256(b"timing-doc").hexdigest(),
        mime="application/pdf",
        doc_class="DPR",
        confidence=0.99,
        needs_vision=False,
        pages=[{"page": 1, "text": "Project cost is 80 lakh and loan is 60 lakh."}],
    )

    # A real Settings() (not a bare SimpleNamespace missing attributes like
    # cam_engine_enabled) so the run actually reaches COMPLETED rather than
    # failing on an AttributeError before any analysis happens.
    settings = Settings(
        _env_file=None,
        dpr_agentic_vision_enabled=False,
        dpr_agentic_vision_max_pages=20,
        dpr_agentic_liteparse_page_min_chars=50,
        cam_engine_enabled=False,
        dpr_cam_pdf_render_timeout_seconds=20.0,
    )

    with patch.object(dpr_worker, "inspect_document", return_value=parsed):
        asyncio.run(
            dpr_worker.process_one(dpr_pg, KEY, _FastGemini(), run_id, settings)
        )

    run = get_run(dpr_pg, run_id)
    assert run["status"] == "COMPLETED"

    text = "\n".join(r.getMessage() for r in caplog.records)
    match = re.search(
        rf"dpr_upload_to_report_ms run_id={re.escape(run_id)} duration_ms=(\d+)", text
    )
    assert match, f"no dpr_upload_to_report_ms log line found in: {text}"
    duration_ms = int(match.group(1))
    # Non-negative and sane upper bound for a synthetic, no-real-Gemini run
    # (a few seconds of DB + subprocess PDF-render overhead at most) -- this
    # is an infra floor only, not a real-Gemini-latency measurement (see
    # PROGRESS.md: a real Gemini call is a separate, billed cost that needs
    # the user's go-ahead each time, same as moving money does).
    assert 0 <= duration_ms < 60_000
