"""Milestone 4 (parts 4 and 4c): Page 4 (Repayment) template.

The original design's five-point acreage bar chart needs a realistic
harvest-window range this pipeline does not compute (see
build_page4_context's docstring and PROGRESS.md) -- these tests confirm
the page renders a table of the two real scenarios the engine actually
produces, each with its own DSCR and three break-even figures under that
scenario's own cost assumption, never a fabricated chart or an invented
"AIF subvention" repayment row.
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


def test_scenarios_show_breakeven_per_scenario_under_its_own_cost_assumption():
    """Milestone 4c: each scenario's break-even uses that scenario's own
    running-cost-per-acre -- "Using your DPR's costs" (750/acre, the DPR's
    own stated figure) vs "If flagged fuel and crew corrections are
    confirmed" (900/acre, the corrected figure) -- never mixed. The
    accounting break-even values (622 / 726) match the design reference's
    own "622 -> 726" correction exactly; the debt-service threshold values
    (483 / 563) do not match the design reference's own un-reproducible
    910-acre figure and are reported as computed, not forced to match."""
    ctx4 = build_page4_context(_golden_snapshot())
    by_label = {s["label"]: s for s in ctx4["scenarios"]}
    assert len(by_label) == 2
    as_written = by_label["Using your DPR's costs"]
    corrected = by_label["If flagged fuel and crew corrections are confirmed"]

    assert as_written["is_hypothetical"] is False
    assert as_written["dscr"] == "1.97×"
    assert as_written["breakeven_accounting"] == "622 acres/year"
    assert as_written["breakeven_operating_cashflow"] == "193 acres/year"
    assert as_written["breakeven_debt_service_threshold"] == "483 acres/year"

    assert corrected["is_hypothetical"] is True
    assert corrected["dscr"] == "1.49×"
    assert corrected["breakeven_accounting"] == "726 acres/year"
    assert corrected["breakeven_operating_cashflow"] == "226 acres/year"
    assert corrected["breakeven_debt_service_threshold"] == "563 acres/year"


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
