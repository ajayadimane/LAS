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
from app.services.dpr_agentic.migrate import core_engine  # noqa: E402
from app.services.dpr_agentic.report_store import (  # noqa: E402
    check_pdf_renderer_ready,
    rerender_failed_pdfs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", help="only this run's report")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    if not check_pdf_renderer_ready():
        print(
            "PDF renderer dependencies missing in the active Python environment. "
            "Run: python -m pip install -e .  "
            "Then: python -c 'import jinja2, weasyprint'",
            file=sys.stderr,
        )
        return 2
    if not settings.dpr_database_url:
        sys.exit("rerender_cam_pdfs: DPR_DATABASE_URL is not set")
    engine = core_engine(settings)  # 009 must be in place for the update
    try:
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
