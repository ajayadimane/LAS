"""Offline test that the canonical stored CAMSnapshot renders as PDF.

Usage: python scripts/smoke_test_canonical_cam.py
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.dpr_agentic.cam_adapter import build_snapshot_from_analysis
from app.services.dpr_agentic.report_store import _render_cam_pdf
from app.services.dpr_agentic.schemas import AnalysisResult


def main():
    result = AnalysisResult(service_request_id="offline", wallet_document_id="test-doc",
        document_sha256="a"*64, status="completed", agent_outputs={},
        findings=[], unresolved_questions=[])
    snapshot = build_snapshot_from_analysis(result, report_id="offline-ref-v1",
                                            report_version=1, analysis_run_id="offline-run")
    pdf = _render_cam_pdf(result, run_id="offline-run", source_reference="offline-ref",
                          version=1, snapshot_json=snapshot.model_dump_json(), timeout_seconds=60)
    assert pdf and pdf.startswith(b"%PDF-"), "canonical CAM renderer returned no valid PDF"
    print("PASS canonical snapshot PDF: %s bytes; no Gemini" % len(pdf))


if __name__ == "__main__":
    main()
