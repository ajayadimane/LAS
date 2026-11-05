"""Integration tests for scripts/dpr_worker.py's vision fallback (Phase C).

Loads dpr_worker.py the same way tests/test_dpr_report_access.py does
(scripts/ isn't a package). Needs the dpr_pg Postgres fixtures.
"""

import asyncio
import hashlib
import importlib.util
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

KEY = Fernet.generate_key()

_WORKER_SPEC = importlib.util.spec_from_file_location(
    "dpr_worker_vision_test",
    Path(__file__).resolve().parents[1] / "scripts" / "dpr_worker.py",
)
dpr_worker = importlib.util.module_from_spec(_WORKER_SPEC)
_WORKER_SPEC.loader.exec_module(dpr_worker)


class FakeGemini:
    def __init__(self, ocr_text: str = "Detailed project report cost 100"):
        self.ocr_text = ocr_text
        self.ocr_calls = 0

    async def generate(self, prompt, context):
        return AgentOutput(summary="checked")

    async def extract(self, prompt, context):
        return ProjectFinancials()

    async def ocr_page(self, image_bytes, mime_type="image/png"):
        self.ocr_calls += 1
        return self.ocr_text


def _scanned_pdf_result(sha: str, confidence: float = 0.0, doc_class: str = "OTHER"):
    """What inspect_document() returns for a page with no extractable text.

    low_text_pages=None simulates LiteParse itself being unavailable here --
    the whole-document Gemini vision fallback these tests exercise."""
    return SimpleNamespace(
        sha256=sha,
        mime="application/pdf",
        doc_class=doc_class,
        confidence=confidence,
        needs_vision=True,
        low_text_pages=None,
        pages=[{"page": 1, "text": ""}],
    )


def _submit_and_stage(engine, sha: str):
    run_id, _ = submit(
        engine,
        service_request_id="SR-1",
        document_sha256=sha,
        source_reference="wallet-person-custom-field:9000000013:GEO_CHECK",
    )
    stage_upload(
        engine,
        run_id,
        StoredFile(name="scan.pdf", content_type="application/pdf", data=b"dummy"),
        KEY,
    )
    return run_id


def _settings(**overrides) -> Settings:
    defaults = dict(environment="test", context_path="", dpr_agentic_enabled=True)
    defaults.update(overrides)
    return Settings(**defaults)


def test_vision_disabled_fails_cleanly_without_calling_gemini_vision(dpr_pg):
    sha = hashlib.sha256(b"scanned").hexdigest()
    run_id = _submit_and_stage(dpr_pg, sha)
    gemini = FakeGemini()
    settings = _settings(dpr_agentic_vision_enabled=False)

    with patch.object(
        dpr_worker, "inspect_document", return_value=_scanned_pdf_result(sha)
    ):
        asyncio.run(dpr_worker.process_one(dpr_pg, KEY, gemini, run_id, settings))

    assert gemini.ocr_calls == 0
    row = get_run(dpr_pg, run_id)
    assert row["status"] == "FAILED"
    assert row["error_code"] == "NeedsVisionFallback"


def test_vision_enabled_recovers_a_scanned_dpr(dpr_pg):
    sha = hashlib.sha256(b"scanned-dpr").hexdigest()
    run_id = _submit_and_stage(dpr_pg, sha)
    gemini = FakeGemini(
        ocr_text="This is our Detailed Project Report. Cost of project is stated below."
    )
    settings = _settings(
        dpr_agentic_vision_enabled=True, dpr_agentic_vision_max_pages=5
    )

    with (
        patch.object(
            dpr_worker, "inspect_document", return_value=_scanned_pdf_result(sha)
        ),
        patch.object(
            dpr_worker, "render_pages_to_images", return_value=[b"page-image"]
        ),
    ):
        asyncio.run(dpr_worker.process_one(dpr_pg, KEY, gemini, run_id, settings))

    assert gemini.ocr_calls == 1
    row = get_run(dpr_pg, run_id)
    assert row["status"] == "COMPLETED"


def test_vision_enabled_but_blank_ocr_still_fails_cleanly(dpr_pg):
    sha = hashlib.sha256(b"blank-scan").hexdigest()
    run_id = _submit_and_stage(dpr_pg, sha)
    gemini = FakeGemini(ocr_text="")  # vision ran, but the page was actually blank
    settings = _settings(dpr_agentic_vision_enabled=True)

    with (
        patch.object(
            dpr_worker, "inspect_document", return_value=_scanned_pdf_result(sha)
        ),
        patch.object(
            dpr_worker, "render_pages_to_images", return_value=[b"page-image"]
        ),
    ):
        asyncio.run(dpr_worker.process_one(dpr_pg, KEY, gemini, run_id, settings))

    row = get_run(dpr_pg, run_id)
    assert row["status"] == "FAILED"
    assert row["error_code"] == "NeedsVisionFallback"


def test_vision_enabled_but_ocr_text_is_not_a_confident_dpr(dpr_pg):
    """Recovering the text doesn't automatically mean it's a DPR."""
    sha = hashlib.sha256(b"scanned-pan-card").hexdigest()
    run_id = _submit_and_stage(dpr_pg, sha)
    gemini = FakeGemini(ocr_text="Permanent Account Number. Income Tax Department.")
    settings = _settings(dpr_agentic_vision_enabled=True)

    with (
        patch.object(
            dpr_worker, "inspect_document", return_value=_scanned_pdf_result(sha)
        ),
        patch.object(
            dpr_worker, "render_pages_to_images", return_value=[b"page-image"]
        ),
    ):
        asyncio.run(dpr_worker.process_one(dpr_pg, KEY, gemini, run_id, settings))

    row = get_run(dpr_pg, run_id)
    assert row["status"] == "FAILED"
    assert row["error_code"] == "ClassificationReviewRequired"


def test_vision_enabled_but_document_cannot_be_rendered_fails_cleanly(dpr_pg):
    sha = hashlib.sha256(b"unrenderable").hexdigest()
    run_id = _submit_and_stage(dpr_pg, sha)
    gemini = FakeGemini()
    settings = _settings(dpr_agentic_vision_enabled=True)

    with (
        patch.object(
            dpr_worker, "inspect_document", return_value=_scanned_pdf_result(sha)
        ),
        patch.object(dpr_worker, "render_pages_to_images", return_value=[]),
    ):
        asyncio.run(dpr_worker.process_one(dpr_pg, KEY, gemini, run_id, settings))

    assert gemini.ocr_calls == 0
    row = get_run(dpr_pg, run_id)
    assert row["status"] == "FAILED"
    assert row["error_code"] == "NeedsVisionFallback"
