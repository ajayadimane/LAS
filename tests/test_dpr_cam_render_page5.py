"""Milestone 4 (part 5): Page 5 (Security and obligations) template.

CAMSnapshot has no collateral/guarantor model, so those rows must read
"Not assessed by this analysis" -- never the original design's "Not
stated in your DPR" (a claim we checked the document and found it
absent, which this pipeline never did). The post-delivery checklist is
generic advisory content and must only appear for the harvester sector
this pipeline actually models.
"""

import json
from pathlib import Path

from app.services.dpr_agentic.cam_adapter import build_snapshot_from_analysis
from app.services.dpr_agentic.cam_render import (
    build_page5_context,
    render_cam_report_html,
    render_cam_report_pdf,
)
from app.services.dpr_agentic.cam_snapshot import CAMSnapshot, Money, ProjectProfile
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


def test_collateral_and_guarantor_never_claim_the_document_says_nothing():
    ctx5 = build_page5_context(_golden_snapshot())
    by_label = {r["label"]: r for r in ctx5["security_rows"]}
    assert by_label["Land or other collateral"]["details"] == (
        "Not assessed by this analysis"
    )
    assert by_label["Guarantor"]["details"] == "Not assessed by this analysis"
    html = render_cam_report_html(_golden_snapshot())
    assert "Not stated in your DPR" not in html


def test_harvester_sector_shows_the_post_delivery_checklist():
    ctx5 = build_page5_context(_golden_snapshot())
    assert len(ctx5["after_delivery_checklist"]) == 5


def test_non_harvester_sector_shows_an_explicit_unavailable_note():
    snapshot = _golden_snapshot()
    snapshot = snapshot.model_copy(
        update={
            "project_profile": ProjectProfile(
                project_name="Dairy unit",
                business_subsector="Dairy",
                machinery_equipment=["Milking machine"],
            )
        }
    )
    ctx5 = build_page5_context(snapshot)
    assert ctx5["after_delivery_checklist"] == []
    html = render_cam_report_html(snapshot)
    assert "not yet available for this sector" in html


def test_disbursement_steps_use_real_figures_when_available():
    snapshot = _golden_snapshot()
    snapshot = snapshot.model_copy(
        update={
            "means_of_finance": snapshot.means_of_finance.model_copy(
                update={
                    "requested_loan": Money(amount=22.50, unit="lakh"),
                    "promoter_contribution": Money(amount=7.50, unit="lakh"),
                }
            )
        }
    )
    ctx5 = build_page5_context(snapshot)
    assert "7.50 lakh" in ctx5["disbursement_steps"][0]["detail"]
    assert "22.50 lakh" in ctx5["disbursement_steps"][1]["detail"]


def test_disbursement_steps_degrade_gracefully_without_figures():
    ctx5 = build_page5_context(_empty_snapshot())
    assert "contribution" in ctx5["disbursement_steps"][0]["detail"]
    assert "loan amount" in ctx5["disbursement_steps"][1]["detail"]


def test_page5_has_no_unresolved_jinja_placeholders():
    html = render_cam_report_html(_golden_snapshot())
    assert "{{" not in html
    assert "}}" not in html


def test_page5_has_no_canvas_runtime_markup():
    html = render_cam_report_html(_golden_snapshot())
    for marker in ("<x-dc", "<sc-if", "<sc-for", "support.js", "data-props"):
        assert marker not in html


def test_page5_shows_page_5_of_6():
    html = render_cam_report_html(_golden_snapshot())
    assert "Page 5 of 6" in html


def test_page5_renders_to_a_real_pdf():
    pdf_bytes = render_cam_report_pdf(_golden_snapshot())
    assert pdf_bytes[:5] == b"%PDF-"


def test_the_disbursement_step_connector_line_renders_as_a_thin_line_not_a_block():
    """Regression: a first attempt at the connector used a `::before`
    pseudo-element directly on the CSS grid container, which WeasyPrint
    rendered as a solid filled grid cell rather than a thin line. The
    fix uses a real sibling element outside the grid -- this just checks
    the markup shape that avoids it, since pixel color isn't practical to
    assert from the PDF text layer."""
    html = render_cam_report_html(_golden_snapshot())
    assert "cam-steps-wrap__line" in html
    assert "cam-steps::before" not in html
