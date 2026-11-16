"""Offline smoke test for CAM PDF renderer: no DB, Wallet or Gemini.

Run: python scripts/smoke_test_cam_renderer.py
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.dpr_agentic.report_store import (
    _render_cam_pdf, check_pdf_renderer_ready,
)
from app.services.dpr_agentic.schemas import AnalysisResult, AgentOutput


def main() -> int:
    if not check_pdf_renderer_ready():
        return 2
    report = AnalysisResult(
        service_request_id="RENDERER-SMOKE-TEST",
        wallet_document_id="LOCAL-TEST",
        document_sha256="a" * 64,
        status="completed",
        agent_outputs={"document": AgentOutput(summary="Local PDF rendering test")},
        findings=[],
        unresolved_questions=[],
    )
    pdf = _render_cam_pdf(
        report, run_id="offline-test", source_reference="offline-test",
        version=1, timeout_seconds=40,
    )
    if pdf is None or not pdf.startswith(b"%PDF-"):
        print("ERROR: CAM PDF subprocess failed; see dpr_cam_pdf_render_failed log")
        return 1
    print(f"OK: CAM subprocess rendered {len(pdf)} PDF bytes without Gemini")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
