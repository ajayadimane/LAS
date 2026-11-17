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
from app.services.dpr_agentic.document_input import (  # noqa: E402,F401
    ClassificationNotReady,
    ClassificationReviewRequired,
    NeedsVisionFallback,
    prepare_document,
)
from app.services.dpr_agentic.document_pipeline.liteparse_client import (  # noqa: E402
    LiteParseClient,
)
from app.services.dpr_agentic.document_pipeline.pipeline import (  # noqa: E402
    inspect_document,
)
from app.services.dpr_agentic.document_pipeline.vision import (  # noqa: E402
    extract_pages_via_vision,
    render_pages_to_images,
)
from app.services.dpr_agentic.gemini import (  # noqa: E402
    CURRENT_RUN_ID,
    CURRENT_RUN_USAGE,
    GeminiAgentClient,
)
from app.services.dpr_agentic.grounding import (  # noqa: E402
    PostgresGroundingCache,
    purge_expired_grounding_cache,
)
from app.services.dpr_agentic.jobs import (  # noqa: E402
    fail_stuck_run,
    get_run,
    list_claimable,
    reap_exhausted,
    record_token_usage,
)
from app.services.dpr_agentic.migrate import core_engine  # noqa: E402
from app.services.dpr_agentic.ocr_cache import (  # noqa: E402
    PostgresOcrCache,
    purge_expired_ocr_cache,
)
from app.services.dpr_agentic.report_store import store_report  # noqa: E402
from app.services.dpr_agentic.run_cost import (  # noqa: E402
    price_rates,
    prices_from_settings,
    run_cost_usd,
    sum_usage,
    to_inr,
)
from app.services.dpr_agentic.schemas import AnalysisInput, SourcePage  # noqa: E402
from app.services.dpr_agentic.upload_staging import (  # noqa: E402
    delete_staged_upload,
    load_staged_project_context,
    load_staged_upload,
)
from app.services.dpr_agentic.worker import run_claimed_analysis  # noqa: E402

logger = get_logger(__name__)

POLL_SECONDS = 10
LEASE_SECONDS = 1800  # generous: a 10-role pipeline over a real DPR is slow.


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
        thinking_budget=settings.dpr_agentic_thinking_budget,
        thinking_level=settings.dpr_agentic_thinking_level,
        max_output_tokens=settings.dpr_agentic_max_output_tokens,
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
        # Module-level names, looked up at call time, so tests that patch
        # dpr_worker.inspect_document / render_pages_to_images /
        # extract_pages_via_vision still take effect.
        run = get_run(engine, rid)
        prepared = await prepare_document(
            staged.data,
            staged.name,
            settings=settings,
            gemini=gemini,
            liteparse=liteparse,
            run_id=rid,
            inspect=inspect_document,
            render=render_pages_to_images,
            ocr=extract_pages_via_vision,
            document_sha256=run["document_sha256"],
            ocr_cache=(
                PostgresOcrCache(engine, key, settings.dpr_ocr_cache_ttl_days)
                if settings.dpr_ocr_cache_ttl_days > 0
                else None
            ),
        )
        return AnalysisInput(
            service_request_id=run["service_request_id"],
            wallet_document_id=run["source_reference"],
            document_sha256=run["document_sha256"],
            pages=[SourcePage(**p) for p in prepared.pages],
            # Service details (bank name...) staged at upload; {} if none.
            project_context=load_staged_project_context(engine, rid, key),
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
    usage_log: list[dict] = []
    usage_token = CURRENT_RUN_USAGE.set(usage_log)
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
            grounding_enabled=settings.dpr_agentic_grounding_enabled,
            grounding_cache=(
                PostgresGroundingCache(engine, settings.dpr_grounding_cache_ttl_days)
                if settings.dpr_grounding_cache_ttl_days > 0
                else None
            ),
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
        # Logged regardless of success/failure -- a config error that fails
        # the run before any Gemini call just reports zero, a failure partway
        # through (e.g. a bad role) still reports whatever was spent getting
        # there. Full end-to-end total for this run, from the first call
        # (classification/vision/financial extraction) through the last
        # orchestrator role; the PDF render itself calls no LLM, so this is
        # also the total for upload-to-report-ready. Per-call detail is the
        # dpr_gemini_call log line in gemini.py, already emitted as each call
        # happens -- this is the one-line rollup for the whole run.
        usage = sum_usage(usage_log)
        # The cost at the configured rates (run_cost.py); never allowed to
        # stop the token totals being stored.
        cost_usd = cost_inr = prices = None
        usd_to_inr = getattr(settings, "dpr_usd_to_inr", None)
        try:
            prices = prices_from_settings(settings)
            if prices is not None:
                cost_usd = run_cost_usd(usage, prices)
                cost_inr = to_inr(cost_usd, usd_to_inr)
        except Exception:  # noqa: BLE001
            logger.warning("dpr_run_cost_failed run_id=%s", run_id, exc_info=True)
        logger.info(
            "dpr_run_token_usage run_id=%s gemini_calls=%d total_prompt_tokens=%d "
            "total_completion_tokens=%d total_tokens=%d thinking_tokens=%d "
            "cached_tokens=%d search_queries=%d cost_usd=%s cost_inr=%s",
            run_id,
            usage.gemini_calls,
            usage.prompt_tokens,
            usage.completion_tokens,
            usage.total_tokens,
            usage.thinking_tokens,
            usage.cached_tokens,
            usage.search_queries,
            cost_usd,
            cost_inr,
        )
        # Persisted (migrations 008, 013) so the totals and the cost are
        # queryable per run, not just grep-able in logs. Unconditional: the
        # run has already reached its terminal state by this point (finish()
        # or fail_stuck_run() above already ran), so there's no lease/status
        # race to guard against here.
        record_token_usage(
            engine,
            run_id,
            gemini_calls=usage.gemini_calls,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            thinking_tokens=usage.thinking_tokens,
            cached_tokens=usage.cached_tokens,
            search_queries=usage.search_queries,
            gemini_model=getattr(gemini, "model", None),
            cost_usd=cost_usd,
            cost_inr=cost_inr,
            usd_to_inr=usd_to_inr if cost_inr is not None else None,
            price_rates=price_rates(prices) if cost_usd is not None else None,
        )
        CURRENT_RUN_USAGE.reset(usage_token)
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
    try:
        purged = purge_expired_ocr_cache(engine)
        if purged:
            logger.info("dpr_ocr_cache_purged rows=%d", purged)
    except Exception:
        logger.warning("dpr_ocr_cache_purge_failed", exc_info=True)
    try:
        purge_expired_grounding_cache(engine)
    except Exception:
        logger.warning("dpr_grounding_cache_purge_failed", exc_info=True)
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

    engine = core_engine(settings)
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
