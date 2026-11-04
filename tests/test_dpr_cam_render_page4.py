"""Milestone 4 (part 4): Page 4 (Repayment) template.

The original design's five-point acreage bar chart needs a realistic
harvest-window range and a DSCR=1.0 break-even acreage this pipeline does
not compute yet (see build_page4_context's docstring and PROGRESS.md) --
these tests confirm the page renders only the two real scenarios the
engine actually produces, never a fabricated chart or an invented "AIF
subvention" repayment row.
"""

import json
from pathlib import Path

from app.services.dpr_agentic.cam_adapter import build_snapshot_from_analysis
from app.services.dpr_agentic.cam_render import (
    build_page4_context,
    render_cam_report_html,
    render_cam_report_pdf,
)
from app.services.dpr_agentic.cam_snapshot import CAMSnapshot
from app.services.dpr_agentic.schemas import AnalysisResult

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "tikamgarh_cam_snapshot.json"


def _golden_snapshot() -> CAMSnapshot:
    return CAMSnapshot.model_validate(json.loads(FIXTURE_PATH.read_text()))


def _empty_snapshot() -> CAMSnapshot:
    result = AnalysisResult(
        service_request_id="SR-EMPTY",
        wallet_document_id="W-EMPTY",
        document_sha256="b" * 64,
        status="completed",
        agent_outputs={},
        findings=[],
        unresolved_questions=[],
    )
    return build_snapshot_from_analysis(
        result, report_id="RPT-EMPTY", report_version=1, analysis_run_id="RUN-EMPTY"
    )


def test_dscr_cards_show_both_real_conventions():
    ctx4 = build_page4_context(_golden_snapshot())
    labels = {c["label"]: c["value"] for c in ctx4["dscr_cards"]}
    assert labels["Your DPR states (Year 1)"] == "1.58×"
    assert labels["Standard bank formula (Year 1)"] == "1.97×"


def test_breakeven_threshold_shows_the_engines_own_computed_value():
    """Milestone 4b: wired from cam_engine_bridge.py's
    debt_service_threshold_breakeven(), now that it's called. The value is
    whatever the engine actually computes for the golden case -- not
    forced to match the design reference's own un-reproducible 910-acre
    figure."""
    ctx4 = build_page4_context(_golden_snapshot())
    assert ctx4["breakeven_threshold"] == "563 acres/year"
    html = render_cam_report_html(_golden_snapshot())
    assert "563 acres/year" in html


def test_scenarios_include_both_real_scenarios_and_flag_the_hypothetical_one():
    ctx4 = build_page4_context(_golden_snapshot())
    by_label = {s["label"]: s for s in ctx4["scenarios"]}
    as_written = next(s for s in ctx4["scenarios"] if "stated in the DPR" in s["label"])
    corrected = next(s for s in ctx4["scenarios"] if "Corrected" in s["label"])
    assert as_written["is_hypothetical"] is False
    assert as_written["dscr"] == "1.97×"
    assert corrected["is_hypothetical"] is True
    assert corrected["dscr"] == "1.49×"
    assert len(by_label) == 2


def test_year1_repayment_has_no_fabricated_subvention_row():
    """The original design's "With AIF subvention" row has no backing
    data anywhere in CAMSnapshot and must never appear."""
    html = render_cam_report_html(_golden_snapshot())
    assert "AIF" not in html
    assert "subvention" not in html


def test_empty_snapshot_shows_explicit_empty_states_not_fabricated_data():
    ctx4 = build_page4_context(_empty_snapshot())
    assert ctx4["dscr_cards"] == []
    assert ctx4["scenarios"] == []
    assert ctx4["year1"] is None
    assert ctx4["breakeven_threshold"] is None
    html = render_cam_report_html(_empty_snapshot())
    assert "No repayment scenarios are available" in html
    assert "A Year-1 repayment schedule is not yet available" in html


def test_page4_has_no_unresolved_jinja_placeholders():
    html = render_cam_report_html(_golden_snapshot())
    assert "{{" not in html
    assert "}}" not in html


def test_page4_has_no_canvas_runtime_markup():
    html = render_cam_report_html(_golden_snapshot())
    for marker in ("<x-dc", "<sc-if", "<sc-for", "support.js", "data-props"):
        assert marker not in html


def test_page4_shows_page_4_of_6():
    html = render_cam_report_html(_golden_snapshot())
    assert "Page 4 of 6" in html


def test_page4_renders_to_a_real_pdf():
    pdf_bytes = render_cam_report_pdf(_golden_snapshot())
    assert pdf_bytes[:5] == b"%PDF-"
