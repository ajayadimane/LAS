#!/usr/bin/env python
"""The one approved real Gemini run: the real Tikamgarh DPR, end to end,
through the real production code path (jobs.submit -> upload_staging ->
scripts/dpr_worker.process_one -> a real GeminiAgentClient -> real
report_store.store_report), with CAM_ENGINE_ENABLED=true.

Approved by the user, 2026-11-05, as the last step of milestone 7, after
confirming no real Tikamgarh DPR file existed anywhere in this repo until
this run's input: DPRData/Approved DPR /TikamgarhDPR.pdf. This is a real,
billed Gemini call (11 calls: one extraction + one generate() per of 10
orchestrator roles) -- not a simulation, not mocked, not respx-faked. The
vendor (iWallet) is never contacted: this worker path never calls it, by
design (see scripts/dpr_worker.py's module docstring).

Writes one real row to dpr_analysis_runs/dpr_report_artifacts in the
database named by DPR_DATABASE_URL in .env (Core LAS' own Postgres, never
Wallet's) under a clearly fake, test-only service_request_id/
source_reference -- printed below, not a real customer id.

Usage: python scripts/run_tikamgarh_real.py
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.services.documents import StoredFile  # noqa: E402
from app.services.dpr_agentic.branch_status import init_branches  # noqa: E402
from app.services.dpr_agentic.cam_adapter import build_snapshot_from_analysis  # noqa: E402
from app.services.dpr_agentic.cam_annexures import build_annexures  # noqa: E402
from app.services.dpr_agentic.cam_render import (  # noqa: E402
    build_page1_context,
    build_page2_context,
    build_page3_context,
    build_page4_context,
    build_page5_context,
    build_page6_context,
)
from app.services.dpr_agentic.cam_verification import verify_snapshot  # noqa: E402
from app.services.dpr_agentic.document_pipeline.liteparse_client import (  # noqa: E402
    LiteParseClient,
)
from app.services.dpr_agentic.gemini import GeminiAgentClient  # noqa: E402
from app.services.dpr_agentic.jobs import engine_for, get_run, submit  # noqa: E402
from app.services.dpr_agentic.migrate import apply_migrations  # noqa: E402
from app.services.dpr_agentic.report_store import get_report  # noqa: E402
from app.services.dpr_agentic.upload_staging import stage_upload  # noqa: E402

_WORKER_SPEC = importlib.util.spec_from_file_location(
    "dpr_worker_real_run", Path(__file__).resolve().parent / "dpr_worker.py"
)
dpr_worker = importlib.util.module_from_spec(_WORKER_SPEC)
_WORKER_SPEC.loader.exec_module(dpr_worker)

DPR_PATH = (
    Path(__file__).resolve().parents[1]
    / "DPRData"
    / "Approved DPR "  # trailing space is part of the real directory name
    / "TikamgarhDPR.pdf"
)
SERVICE_REQUEST_ID = "TEST-TIKAMGARH-REAL-RUN:GEO_CHECK"
SOURCE_REFERENCE = "wallet-person-custom-field:TEST-TIKAMGARH-REAL-RUN:GEO_CHECK"


def _missing_states_per_page(snapshot) -> dict[str, int]:
    """Counts "Not available"/"Not yet checked"/"requires verification"
    style strings in each page's own render context -- the same contexts
    the real PDF template renders from, not a separate re-derivation."""
    builders = {
        "page1": build_page1_context,
        "page2": build_page2_context,
        "page3": build_page3_context,
        "page4": build_page4_context,
        "page5": build_page5_context,
        "page6": build_page6_context,
    }
    needles = (
        "not available",
        "not yet checked",
        "not computable",
        "requires verification",
        "insufficient information",
    )
    counts: dict[str, int] = {}
    for name, builder in builders.items():
        ctx = builder(snapshot)
        text = json.dumps(ctx, default=str).lower()
        counts[name] = sum(text.count(n) for n in needles)
    return counts


def main() -> None:
    settings = Settings(cam_engine_enabled=True)
    missing = [
        name
        for name, value in [
            ("DPR_AGENTIC_GEMINI_API_KEY", settings.dpr_agentic_gemini_api_key),
            ("DPR_DATABASE_URL", settings.dpr_database_url),
            ("DPR_STAGING_ENCRYPTION_KEY", settings.dpr_staging_encryption_key),
        ]
        if value is None
    ]
    if missing:
        raise SystemExit(f"Missing required .env settings: {', '.join(missing)}")
    if not DPR_PATH.exists():
        raise SystemExit(f"Document not found: {DPR_PATH}")

    data = DPR_PATH.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    print(f"Document: {DPR_PATH} ({len(data)} bytes, sha256={sha})")

    engine = engine_for(settings.dpr_database_url.get_secret_value())
    apply_migrations(engine)

    run_id, created = submit(
        engine,
        service_request_id=SERVICE_REQUEST_ID,
        document_sha256=sha,
        source_reference=SOURCE_REFERENCE,
    )
    print(f"submit(): run_id={run_id} created={created}")
    init_branches(engine, run_id)
    key = settings.dpr_staging_encryption_key.get_secret_value().encode()
    stage_upload(
        engine,
        run_id,
        StoredFile(name=DPR_PATH.name, content_type="application/pdf", data=data),
        key,
    )

    gemini_key = settings.dpr_agentic_gemini_api_key.get_secret_value()
    upload_started_at = datetime.now(timezone.utc)

    async def run() -> None:
        async with httpx.AsyncClient(timeout=120.0) as http:
            gemini = GeminiAgentClient(
                http, gemini_key, settings.dpr_agentic_gemini_model
            )
            liteparse = LiteParseClient()
            started = time.monotonic()
            await dpr_worker.process_one(
                engine, key, gemini, run_id, settings, liteparse=liteparse
            )
            print(f"process_one() wall-clock: {time.monotonic() - started:.1f}s")

    asyncio.run(run())

    run_row = get_run(engine, run_id)
    status, error_code = run_row["status"], run_row["error_code"]
    print(f"\nFinal run state: status={status} error_code={error_code}")
    if run_row["status"] != "COMPLETED":
        raise SystemExit(
            "Run did not complete -- see dpr_worker logs above for the cause."
        )

    upload_to_report_ms = int(
        (datetime.now(timezone.utc) - upload_started_at).total_seconds() * 1000
    )
    print(f"Upload-to-report time (this script's own clock): {upload_to_report_ms}ms")

    result = get_report(engine, SOURCE_REFERENCE)
    snapshot = build_snapshot_from_analysis(
        result,
        report_id=SOURCE_REFERENCE,
        report_version=1,
        analysis_run_id=run_id,
    )

    print("\n=== Golden / headline figures ===")
    fr = snapshot.financial_ratios
    pc = snapshot.project_cost
    mof = snapshot.means_of_finance
    headline = {
        "project_cost_dpr_reported_total": pc.dpr_reported_total,
        "project_cost_recomputed_total": pc.recomputed_total,
        "requested_loan": mof.requested_loan,
        "promoter_contribution": mof.promoter_contribution,
        "proposed_tenure_months": mof.proposed_tenure_months,
        "average_dscr": fr.average_dscr,
        "breakeven_accounting": fr.breakeven_accounting,
        "breakeven_operating_cashflow": fr.breakeven_operating_cashflow,
        "breakeven_debt_service_threshold": fr.breakeven_debt_service_threshold,
    }
    for k, v in headline.items():
        print(f"  {k}: {v}")

    print("\n=== Verification checks (cam_verification.verify_snapshot) ===")
    verification = verify_snapshot(snapshot)
    print(f"  is_publishable={verification.is_publishable}")
    print(f"  total issues={len(verification.issues)}")
    for issue in verification.issues:
        print(f"    [{issue.severity}] {issue.code}: {issue.message} ({issue.path})")

    print("\n=== Missing-state count per page ===")
    for page, count in _missing_states_per_page(snapshot).items():
        print(f"  {page}: {count}")

    print("\n=== Annexure completeness (A-G) ===")
    annexures = build_annexures(snapshot, result, run_row)
    for letter, section in annexures.items():
        print(f"  {letter}: {len(section['missing'])} missing item(s)")
        for m in section["missing"]:
            print(f"      - {m}")

    print(f"\nfindings: {len(snapshot.findings)}")
    print(f"gemini_model used: {result.gemini_model}")
    print(f"run_id={run_id} source_reference={SOURCE_REFERENCE}")


if __name__ == "__main__":
    main()
