#!/usr/bin/env python
"""Standalone DPR analysis worker: claims QUEUED jobs and runs them.

Separate process from the API, by design (see
app/services/dpr_agentic/INCREMENT6.md's Phase A): a multi-role Gemini
pipeline over a real document is a multi-minute job and must not tie up the
process serving dashboard requests, and a crash here must not need the
original caller's (now possibly expired) vendor bearer token -- this worker
never calls the vendor at all, only Gemini and our own Postgres.

Usage:
    python scripts/dpr_worker.py                  # run forever
    python scripts/dpr_worker.py --once            # one poll cycle, then exit

Requires DPR_AGENTIC_ENABLED=true, DPR_DATABASE_URL, DPR_STAGING_ENCRYPTION_KEY
and DPR_AGENTIC_GEMINI_API_KEY in .env; exits with a clear error otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.core.config import Settings, get_settings  # noqa: E402
from app.core.logging import configure_logging, get_logger  # noqa: E402
from app.services.dpr_agentic.branch_status import mark_report_completed  # noqa: E402
from app.services.dpr_agentic.document_pipeline.classify import classify_by_rules  # noqa: E402
from app.services.dpr_agentic.document_pipeline.liteparse_client import (  # noqa: E402
    LiteParseClient,
)
from app.services.dpr_agentic.document_pipeline.pipeline import (  # noqa: E402
    DocumentProcessingError,
    inspect_document,
)
from app.services.dpr_agentic.document_pipeline.vision import (  # noqa: E402
    extract_pages_via_vision,
    render_pages_to_images,
)
from app.services.dpr_agentic.gemini import CURRENT_RUN_ID, GeminiAgentClient  # noqa: E402
from app.services.dpr_agentic.jobs import (  # noqa: E402
    engine_for,
    fail_stuck_run,
    get_run,
    list_claimable,
    reap_exhausted,
)
from app.services.dpr_agentic.migrate import apply_migrations  # noqa: E402
from app.services.dpr_agentic.report_store import store_report  # noqa: E402
from app.services.dpr_agentic.schemas import AnalysisInput, SourcePage  # noqa: E402
from app.services.dpr_agentic.upload_staging import (  # noqa: E402
    delete_staged_upload,
    load_staged_upload,
)
from app.services.dpr_agentic.worker import run_claimed_analysis  # noqa: E402

logger = get_logger(__name__)

POLL_SECONDS = 10
LEASE_SECONDS = 1800  # generous: a 10-role pipeline over a real DPR is slow.


class ClassificationNotReady(Exception):
    """The staged document isn't a confidently-classified, text-bearing DPR."""


class NeedsVisionFallback(ClassificationNotReady):
    """No usable extracted text, and vision OCR is off, failed, or still
    didn't produce a confident DPR classification."""


class ClassificationReviewRequired(ClassificationNotReady):
    pass


def build_dedicated_gemini_client(
    settings: Settings,
) -> tuple[httpx.AsyncClient, GeminiAgentClient]:
    """Its own client, never the vendor's (same reasoning as app.main's
    build_gemini_http_client: verify=True always, independent of
    vendor_verify_ssl). The same client and model handle both narrative
    analysis and vision OCR -- gemini-2.5-flash is multimodal already."""
    http = httpx.AsyncClient(timeout=120.0, verify=True)
    client = GeminiAgentClient(
        http,
        settings.dpr_agentic_gemini_api_key.get_secret_value(),
        settings.dpr_agentic_gemini_model,
    )
    return http, client


async def process_one(
    engine,
    key: bytes,
    gemini: GeminiAgentClient,
    run_id: str,
    settings: Settings,
    *,
    liteparse: LiteParseClient | None = None,
) -> None:
    async def load_input(rid: str) -> AnalysisInput:
        staged = load_staged_upload(engine, rid, key)
        if staged is None:
            raise RuntimeError(
                "Staged upload missing (already consumed or never staged)"
            )
        try:
            parsed = inspect_document(
                staged.data,
                staged.name,
                liteparse=liteparse,
                page_min_chars=settings.dpr_agentic_liteparse_page_min_chars,
            )
        except DocumentProcessingError as exc:
            raise ClassificationReviewRequired(str(exc)) from exc

        pages, doc_class, confidence = parsed.pages, parsed.doc_class, parsed.confidence
        if parsed.needs_vision:
            if not settings.dpr_agentic_vision_enabled:
                raise NeedsVisionFallback(
                    "no usable extracted text; vision is disabled"
                )
            if parsed.low_text_pages is None:
                # LiteParse itself was unavailable/failed -- no per-page
                # signal to target, so fall back across the whole document
                # (bounded by the vision page cap), same as before LiteParse.
                images = render_pages_to_images(
                    staged.data,
                    parsed.mime,
                    max_pages=settings.dpr_agentic_vision_max_pages,
                )
                if not images:
                    raise NeedsVisionFallback(
                        "no usable extracted text and nothing to render"
                    )
                pages = await extract_pages_via_vision(gemini, images)
            else:
                # LiteParse succeeded overall; only recover the specific pages
                # it couldn't, keeping its text for every other page.
                images = render_pages_to_images(
                    staged.data,
                    parsed.mime,
                    max_pages=settings.dpr_agentic_vision_max_pages,
                    pages=parsed.low_text_pages,
                )
                if not images:
                    raise NeedsVisionFallback(
                        "no usable extracted text and nothing to render"
                    )
                recovered = await extract_pages_via_vision(gemini, images)
                # strict=False: render_pages_to_images() silently drops any
                # out-of-range page number, so recovered can be shorter than
                # low_text_pages -- the remaining pages just keep their
                # original (still weak) LiteParse text below.
                recovered_by_page = dict(
                    zip(
                        parsed.low_text_pages,
                        (p["text"] for p in recovered),
                        strict=False,
                    )
                )
                pages = [
                    {
                        "page": p["page"],
                        "text": recovered_by_page.get(p["page"], p["text"]),
                    }
                    for p in pages
                ]
            preview = "\n".join(p["text"] for p in pages)
            if not preview.strip():
                raise NeedsVisionFallback("vision OCR produced no usable text")
            reclassified = classify_by_rules(staged.name, parsed.mime, preview[:30000])
            doc_class, confidence = reclassified.doc_class, reclassified.confidence

        if doc_class != "DPR" or confidence < 0.8:
            raise ClassificationReviewRequired(
                f"classified as {doc_class} ({confidence:.2f})"
            )
        run = get_run(engine, rid)
        return AnalysisInput(
            service_request_id=run["service_request_id"],
            wallet_document_id=run["source_reference"],
            document_sha256=run["document_sha256"],
            pages=[SourcePage(**p) for p in pages if p["text"].strip()],
        )

    async def store_result(rid: str, result) -> str:
        store_report(
            engine,
            rid,
            result,
            pdf_render_timeout_seconds=settings.dpr_cam_pdf_render_timeout_seconds,
        )
        mark_report_completed(engine, rid, rid)
        return rid

    started = time.monotonic()
    run_created_at = (get_run(engine, run_id) or {}).get("created_at")
    run_id_token = CURRENT_RUN_ID.set(run_id)
    try:
        await run_claimed_analysis(
            engine,
            run_id,
            gemini,
            load_input,
            store_result,
            lease_seconds=LEASE_SECONDS,
            heartbeat_interval=LEASE_SECONDS // 3,
            cam_engine_enabled=settings.cam_engine_enabled,
        )
        logger.info(
            "dpr_run_completed run_id=%s duration_ms=%d",
            run_id,
            int((time.monotonic() - started) * 1000),
        )
        # Section 17/the user's own request: not just this worker's own
        # processing time, but the full upload -> report-ready latency a
        # customer actually experiences, including however long the run sat
        # QUEUED before this worker claimed it. created_at is set at
        # submit() time, i.e. the same request that stores the uploaded
        # document (document_queue.upload_and_queue_analysis); the report
        # (PDF included) is already stored by the time this line runs.
        if run_created_at is not None:
            upload_to_report_ms = int(
                (datetime.now(timezone.utc) - run_created_at).total_seconds() * 1000
            )
            logger.info(
                "dpr_upload_to_report_ms run_id=%s duration_ms=%d",
                run_id,
                upload_to_report_ms,
            )
    except Exception as exc:  # noqa: BLE001 - one bad document/config must
        # never kill the poll loop.
        #
        # run_claimed_analysis() marks the run FAILED itself for any error
        # from claim() onward -- but an error raised while *building* this
        # very call (e.g. a misconfigured `settings` missing an attribute
        # this function reads as a keyword argument, below) happens before
        # claim() ever runs, so the run is never touched and stays QUEUED
        # forever, retried identically by every poll cycle (confirmed via
        # test_dpr_log_safety.py's SimpleNamespace settings fixture, which
        # reproduced exactly this). fail_stuck_run() is the backstop: it
        # terminal-fails a QUEUED-or-RUNNING run regardless of lease state,
        # and is a safe no-op if finish() already marked it FAILED or
        # COMPLETED.
        fail_stuck_run(engine, run_id, type(exc).__name__)
        logger.warning(
            "dpr_run_failed run_id=%s duration_ms=%d error=%s",
            run_id,
            int((time.monotonic() - started) * 1000),
            type(exc).__name__,
        )
    finally:
        CURRENT_RUN_ID.reset(run_id_token)
    # Terminal either way (run_claimed_analysis's claim() already succeeded,
    # so finish() always ran): the staged bytes are never needed again.
    delete_staged_upload(engine, run_id)


async def poll_once(
    engine,
    key: bytes,
    gemini: GeminiAgentClient,
    settings: Settings,
    *,
    liteparse: LiteParseClient | None = None,
) -> int:
    for reaped in reap_exhausted(engine):
        delete_staged_upload(engine, reaped)
        logger.warning("dpr_run_attempts_exhausted run_id=%s", reaped)
    run_ids = list_claimable(engine)
    for run_id in run_ids:
        await process_one(engine, key, gemini, run_id, settings, liteparse=liteparse)
    return len(run_ids)


async def run(once: bool) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    missing = [
        name
        for name, value in (
            ("DPR_AGENTIC_ENABLED", settings.dpr_agentic_enabled or None),
            ("DPR_DATABASE_URL", settings.dpr_database_url),
            ("DPR_STAGING_ENCRYPTION_KEY", settings.dpr_staging_encryption_key),
            ("DPR_AGENTIC_GEMINI_API_KEY", settings.dpr_agentic_gemini_api_key),
        )
        if not value
    ]
    if missing:
        sys.exit(f"dpr_worker: missing required settings: {', '.join(missing)}")

    engine = engine_for(settings.dpr_database_url.get_secret_value())
    apply_migrations(engine)
    key = settings.dpr_staging_encryption_key.get_secret_value().encode()
    http, gemini = build_dedicated_gemini_client(settings)
    liteparse = LiteParseClient(
        cli_path=settings.dpr_agentic_liteparse_cli_path,
        ocr_language=settings.dpr_agentic_liteparse_ocr_language,
        timeout=settings.dpr_agentic_liteparse_timeout_seconds,
    )
    liteparse.ensure_available()  # logs a warning only; never fatal at startup
    logger.info(
        "dpr_worker_started once=%s poll_seconds=%s vision_enabled=%s",
        once,
        POLL_SECONDS,
        settings.dpr_agentic_vision_enabled,
    )
    try:
        while True:
            claimed = await poll_once(
                engine, key, gemini, settings, liteparse=liteparse
            )
            if once:
                break
            if claimed == 0:
                await asyncio.sleep(POLL_SECONDS)
    finally:
        await http.aclose()
        engine.dispose()
        logger.info("dpr_worker_stopped")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once", action="store_true", help="run a single poll cycle, then exit"
    )
    args = parser.parse_args()
    asyncio.run(run(args.once))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
