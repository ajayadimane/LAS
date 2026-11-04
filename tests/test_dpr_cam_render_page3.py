"""Milestone 4 (part 3): Page 3 (Project, cost and corrections) template.

project_cost/means_of_finance remain an unclosed gap from milestone 2b
(see PROGRESS.md) -- the golden fixture's breakdown is empty, so these
tests cover both the populated and the still-empty paths: an empty
breakdown must render an explicit note, never a fabricated line, bar or
number. The corrections table is driven by Finding.dpr_stated_value/
recomputed_display_value (fixed in the previous commit) -- never by a
finding's title.
"""

import json
from pathlib import Path

from app.services.dpr_agentic.cam_adapter import build_snapshot_from_analysis
from app.services.dpr_agentic.cam_render import (
    build_page3_context,
    render_cam_report_html,
)
from app.services.dpr_agentic.cam_snapshot import (
    CAMSnapshot,
    FinanceSourceClass,
    MeansOfFinanceLine,
    Money,
    ProjectCostLine,
)
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


def test_empty_cost_breakdown_shows_an_explicit_note_not_a_fabricated_row():
    ctx3 = build_page3_context(_empty_snapshot())
    assert ctx3["cost_rows"] == []
    html = render_cam_report_html(_empty_snapshot())
    assert "No itemized cost breakdown is available" in html


def test_empty_funding_split_shows_an_explicit_note_not_a_fabricated_bar():
    ctx3 = build_page3_context(_empty_snapshot())
    assert ctx3["funding_split"] is None
    html = render_cam_report_html(_empty_snapshot())
    assert "Funding split is not yet available" in html


def test_populated_cost_and_funding_render_real_figures():
    snapshot = _golden_snapshot()
    snapshot = snapshot.model_copy(
        update={
            "project_cost": snapshot.project_cost.model_copy(
                update={
                    "components": [
                        ProjectCostLine(
                            label="AS-930 combine harvester",
                            dpr_reported=Money(amount=30, unit="lakh"),
                        )
                    ],
                    "dpr_reported_total": Money(amount=30, unit="lakh"),
                }
            ),
            "means_of_finance": snapshot.means_of_finance.model_copy(
                update={
                    "dpr_reported_total": Money(amount=30, unit="lakh"),
                    "requested_loan": Money(amount=22.5, unit="lakh"),
                    "promoter_contribution": Money(amount=7.5, unit="lakh"),
                    "components": [
                        MeansOfFinanceLine(
                            label="Promoter contribution",
                            amount=Money(amount=7.5, unit="lakh"),
                            source_class=FinanceSourceClass.UPFRONT_OWN_FUNDED,
                            counted_in_upfront_funding=True,
                        ),
                        MeansOfFinanceLine(
                            label="SBI term loan",
                            amount=Money(amount=22.5, unit="lakh"),
                            source_class=FinanceSourceClass.UPFRONT_LOAN,
                            counted_in_upfront_funding=True,
                        ),
                    ],
                }
            ),
        }
    )
    ctx3 = build_page3_context(snapshot)
    assert ctx3["cost_rows"] == [
        {"label": "AS-930 combine harvester", "amount": "₹ 30.00 lakh"}
    ]
    assert ctx3["cost_total"] == "₹ 30.00 lakh"
    assert ctx3["funding_split"]["loan_pct"] == 75
    assert ctx3["funding_split"]["promoter_pct"] == 25
    html = render_cam_report_html(snapshot)
    assert "AS-930 combine harvester" in html
    assert "75%" in html
    assert "25%" in html


def test_corrections_table_only_includes_findings_with_a_real_value_pair():
    ctx3 = build_page3_context(_golden_snapshot())
    titles = {row["title"] for row in ctx3["corrections"]}
    assert "Fuel cost does not match acres x litres/acre x price" in titles
    assert "Crew wages understated versus the staffing plan" in titles
    # The fund-flow finding has no dpr_stated_value/recomputed_display_value
    # (sources vs. applications is not a "should be" correction) -- must
    # not appear in this table.
    assert "Fund flow sources do not match applications" not in titles


def test_corrections_table_empty_state_is_explicit():
    html = render_cam_report_html(_empty_snapshot())
    assert "No DPR-vs-recomputed corrections are available" in html


def test_page3_has_no_unresolved_jinja_placeholders():
    html = render_cam_report_html(_golden_snapshot())
    assert "{{" not in html
    assert "}}" not in html


def test_page3_has_no_canvas_runtime_markup():
    html = render_cam_report_html(_golden_snapshot())
    for marker in ("<x-dc", "<sc-if", "<sc-for", "support.js", "data-props"):
        assert marker not in html


def test_page3_shows_page_3_of_6():
    html = render_cam_report_html(_golden_snapshot())
    assert "Page 3 of 6" in html


def test_page3_renders_to_a_real_pdf():
    from app.services.dpr_agentic.cam_render import render_cam_report_pdf

    pdf_bytes = render_cam_report_pdf(_golden_snapshot())
    assert pdf_bytes[:5] == b"%PDF-"
