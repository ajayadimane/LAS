#!/usr/bin/env python
"""Renders again the CAM PDF of every stored DPR report whose PDF failed.

Uses the stored report JSON only: no Gemini call, the analysis is not re-run,
and the report's content does not change -- only its missing PDF is filled
in (migration 009 allows exactly that one change). After this, the
dashboard's `.../report?format=pdf` serves the PDF.

Usage:
    python scripts/rerender_cam_pdfs.py                  # every failed PDF
    python scripts/rerender_cam_pdfs.py --run-id <uuid>  # one run's

Needs DPR_DATABASE_URL in .env.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.services.dpr_agentic.jobs import engine_for  # noqa: E402
from app.services.dpr_agentic.migrate import apply_migrations  # noqa: E402
from app.services.dpr_agentic.report_store import rerender_failed_pdfs  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", help="only this run's report")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    if not settings.dpr_database_url:
        sys.exit("rerender_cam_pdfs: DPR_DATABASE_URL is not set")
    engine = engine_for(settings.dpr_database_url.get_secret_value())
    try:
        apply_migrations(engine)  # 009 must be in place for the update
        outcomes = rerender_failed_pdfs(
            engine,
            run_id=args.run_id,
            timeout_seconds=settings.dpr_cam_pdf_render_timeout_seconds,
        )
    finally:
        engine.dispose()

    if not outcomes:
        print("No failed PDFs to render.")
        return 0
    for o in outcomes:
        print(
            f"{o['status']:<16} run {o['run_id']}  "
            f"{o['source_reference']} v{o['version']}"
        )
    return 0 if all(o["status"] != "FAILED" for o in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
