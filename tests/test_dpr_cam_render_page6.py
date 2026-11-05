"""Milestone 4 (part 6, final page): Page 6 (Benefits and next steps).

The original design's AIF interest-subvention rupee figure, credit-
guarantee card, and acreage-specific eligibility rows ("23 of 26 pages",
"0.45x at 600 acres") have no corresponding CAMSnapshot field -- these
tests confirm the page builds a generic, snapshot-driven checklist
instead and never claims a specific bank ("SBI")/branch CAMSnapshot does
not record.
"""

import json
from pathlib import Path

from app.services.dpr_agentic.cam_adapter import build_snapshot_from_analysis
from app.services.dpr_agentic.cam_render import (
    build_page6_context,
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


def test_checklist_reflects_the_real_must_fix_count():
    ctx6 = build_page6_context(_golden_snapshot())
    row = next(
        r
        for r in ctx6["checklist"]
        if r["requirement"] == "Must-fix issues from page 2"
    )
    assert row["found"] == "5 unresolved"
    assert row["met"] is False


def test_checklist_shows_met_when_there_are_no_must_fix_issues():
    ctx6 = build_page6_context(_empty_snapshot())
    row = next(
        r
        for r in ctx6["checklist"]
        if r["requirement"] == "Must-fix issues from page 2"
    )
    assert row["found"] == "None outstanding"
    assert row["met"] is True


def test_action_items_are_empty_when_nothing_is_outstanding():
    ctx6 = build_page6_context(_empty_snapshot())
    assert ctx6["action_items"] == []
    html = render_cam_report_html(_empty_snapshot())
    assert "No further action is needed" in html


def test_action_items_cite_the_real_must_fix_count_not_a_fixed_number():
    ctx6 = build_page6_context(_golden_snapshot())
    assert any("5 must-fix item(s)" in a["title"] for a in ctx6["action_items"])


def test_never_names_a_specific_bank_or_branch():
    """The original design hardcodes "SBI" and "[Branch name]" -- neither
    is tracked anywhere in CAMSnapshot, so naming one would be invented
    specificity (Section 19.3/19.20)."""
    html = render_cam_report_html(_golden_snapshot())
    assert "SBI" not in html
    assert "[Branch name]" not in html


def test_never_fabricates_an_aif_subvention_figure():
    html = render_cam_report_html(_golden_snapshot())
    assert "0.68 lakh" not in html
    assert "interest subvention" not in html.lower()


def test_benefit_card_shows_real_subsidy_when_present():
    from app.services.dpr_agentic.cam_snapshot import Money, SubsidyBenefit

    snapshot = _golden_snapshot()
    snapshot = snapshot.model_copy(
        update={
            "readiness_assessment": snapshot.readiness_assessment.model_copy(
                update={
                    "subsidy_benefits": [
                        SubsidyBenefit(
                            scheme_name="AIF",
                            eligibility_status="verified",
                            eligible_amount=Money(amount=1.5, unit="lakh"),
                        )
                    ]
                }
            )
        }
    )
    ctx6 = build_page6_context(snapshot)
    assert ctx6["benefit_card"]["label"] == "AIF benefit"
    assert ctx6["benefit_card"]["value"] == "₹ 1.50 lakh"


def test_page6_has_no_unresolved_jinja_placeholders():
    html = render_cam_report_html(_golden_snapshot())
    assert "{{" not in html
    assert "}}" not in html


def test_page6_has_no_canvas_runtime_markup():
    html = render_cam_report_html(_golden_snapshot())
    for marker in ("<x-dc", "<sc-if", "<sc-for", "support.js", "data-props"):
        assert marker not in html


def test_page6_shows_page_6_of_6():
    html = render_cam_report_html(_golden_snapshot())
    assert "Page 6 of 6" in html


def test_page6_renders_to_a_real_pdf():
    pdf_bytes = render_cam_report_pdf(_golden_snapshot())
    assert pdf_bytes[:5] == b"%PDF-"


def test_full_six_page_report_renders_with_no_unresolved_placeholders():
    """End-to-end sanity check now that all six pages are wired into
    report.html.jinja."""
    html = render_cam_report_html(_golden_snapshot())
    for marker in ("{{", "}}", "<x-dc", "support.js"):
        assert marker not in html
    # Page 1's header shows "Version N" instead of a page label (see
    # page1_summary.html.jinja); pages 2-6 all show "Page N of 6".
    for page_label in [f"Page {n} of 6" for n in range(2, 7)]:
        assert page_label in html
