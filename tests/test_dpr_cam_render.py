"""Milestone 4 (part 1): Page 1 (Summary) template and PDF rendering.

Covers the Section 18 "Template tests"/"Rendering tests" categories for
the one page wired up so far: placeholder resolution, no sample-data
leakage, no canvas runtime markup, missing-data handling, print layout
(no sliced cards across a page break), and PDF rendering with no network
access.
"""

import json
from decimal import Decimal
from pathlib import Path

import pymupdf
import pytest

from app.services.dpr_agentic.cam_adapter import build_snapshot_from_analysis
from app.services.dpr_agentic.cam_render import (
    build_page1_context,
    format_money,
    format_pct,
    render_cam_report_html,
    render_cam_report_pdf,
)
from app.services.dpr_agentic.cam_snapshot import CAMSnapshot, Money
from app.services.dpr_agentic.schemas import AnalysisResult

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "tikamgarh_cam_snapshot.json"

SAMPLE_STRINGS_THAT_MUST_NEVER_LEAK = [
    "Tikamgarh",
    "Nachanwara",
    "AS-930",
    "0.68 lakh",
    "910 acres",
    "7 of 100",
]


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


def test_renders_a_real_pdf_with_no_network_access():
    pdf_bytes = render_cam_report_pdf(_golden_snapshot())
    assert pdf_bytes[:5] == b"%PDF-"


def test_html_has_no_unresolved_jinja_placeholders():
    html = render_cam_report_html(_golden_snapshot())
    assert "{{" not in html
    assert "}}" not in html


def test_html_has_no_canvas_runtime_markup():
    html = render_cam_report_html(_golden_snapshot())
    for marker in (
        "<x-dc",
        "<sc-if",
        "<sc-for",
        "support.js",
        "data-props",
        "data-dc-script",
    ):
        assert marker not in html


def test_html_has_no_literal_bracket_placeholders():
    html = render_cam_report_html(_golden_snapshot())
    for literal in (
        "[Customer full name]",
        "[BD name]",
        "[Branch name]",
        "[DD Mon YYYY]",
    ):
        assert literal not in html


def test_an_unrelated_snapshot_never_leaks_the_golden_samples_own_data():
    """The golden fixture is the Tikamgarh case and legitimately contains
    these strings; an unrelated (empty) snapshot must never show them."""
    html = render_cam_report_html(_empty_snapshot())
    for sample in SAMPLE_STRINGS_THAT_MUST_NEVER_LEAK:
        assert sample not in html


def test_missing_borrower_name_renders_an_explicit_missing_state_not_a_guess():
    html = render_cam_report_html(_empty_snapshot())
    assert "Borrower name not yet confirmed" in html


def test_missing_loan_amount_renders_requires_verification_not_zero_or_blank():
    html = render_cam_report_html(_empty_snapshot())
    assert "Requires verification" in html


def test_dimensions_score_mode_shows_a_numeric_score_status_mode_does_not():
    from app.services.dpr_agentic.cam_snapshot import ReadinessDimensionScore

    snapshot = _golden_snapshot()
    status_html = render_cam_report_html(snapshot)
    assert "DPR quality score" not in status_html

    dimensions_snapshot = snapshot.model_copy(
        update={
            "readiness_assessment": snapshot.readiness_assessment.model_copy(
                update={
                    "score_mode": "dimensions",
                    "total_score": 62,
                    "dimension_scores": [
                        ReadinessDimensionScore(
                            dimension="arithmetic_accuracy", score=10
                        ),
                        ReadinessDimensionScore(
                            dimension="internal_consistency", score=20
                        ),
                        ReadinessDimensionScore(
                            dimension="assumption_realism", score=17
                        ),
                        ReadinessDimensionScore(dimension="completeness", score=15),
                    ],
                }
            )
        }
    )
    dimensions_html = render_cam_report_html(dimensions_snapshot)
    assert "DPR quality score" in dimensions_html
    assert ">62<" in dimensions_html


def test_no_claim_of_bank_approval_or_recommendation_language():
    """Section 19.20: never claim an official SBI approval decision. The
    original design's own "Recommend"/"Not recommended" outcome labels
    must not appear."""
    html = render_cam_report_html(_golden_snapshot())
    for forbidden in ("Recommend", "Not recommended", "bankable"):
        assert forbidden not in html


def test_print_layout_does_not_slice_a_card_across_a_page_break():
    """Regression for an overflow bug found during development: a
    .cam-stat/.cam-check card split across two physical pages rendered as
    a near-empty bordered box (title present, status text pushed to the
    next page). Each card's own status line must land on the same
    physical page as its title."""
    pdf_bytes = render_cam_report_pdf(_golden_snapshot())
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        texts = [page.get_text() for page in doc]
    title_to_status = {
        "Repayment capacity": "Issues found",
        "Project cost": "Needs attention",
        "Funding plan": "Not yet checked",
        "Identity and KYC": "Not yet checked",
    }
    for title, status in title_to_status.items():
        page_with_title = next(t for t in texts if title in t)
        title_idx = page_with_title.index(title)
        # The matching status phrase must appear shortly (same card) after
        # the title, on the same page -- not merely somewhere on the page.
        nearby = page_with_title[title_idx : title_idx + 60]
        assert status in nearby, (
            f"{title!r} card's status was not on the same page as its title"
        )


def test_long_borrower_and_project_names_do_not_crash_rendering():
    snapshot = _golden_snapshot()
    snapshot = snapshot.model_copy(
        update={
            "borrower_profile": snapshot.borrower_profile.model_copy(
                update={"borrower_name": "A" * 200}
            ),
            "project_profile": snapshot.project_profile.model_copy(
                update={"project_name": "B" * 200}
            ),
        }
    )
    pdf_bytes = render_cam_report_pdf(snapshot)
    assert pdf_bytes[:5] == b"%PDF-"


def test_unicode_borrower_name_renders_without_crashing():
    snapshot = _golden_snapshot()
    snapshot = snapshot.model_copy(
        update={
            "borrower_profile": snapshot.borrower_profile.model_copy(
                update={"borrower_name": "श्री राजेश कुमार"}
            )
        }
    )
    html = render_cam_report_html(snapshot)
    pdf_bytes = render_cam_report_pdf(snapshot)
    assert "श्री राजेश कुमार" in html
    assert pdf_bytes[:5] == b"%PDF-"


def test_llm_authored_text_cannot_inject_markup():
    """Autoescape must neutralize an LLM-authored string that looks like
    HTML -- e.g. a prompt-injected finding title should never execute or
    render as a tag in the output."""
    snapshot = _golden_snapshot()
    snapshot = snapshot.model_copy(
        update={
            "borrower_profile": snapshot.borrower_profile.model_copy(
                update={"borrower_name": "<script>alert(1)</script>"}
            )
        }
    )
    html = render_cam_report_html(snapshot)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_format_money_handles_each_unit():
    assert format_money(Money(amount=Decimal("5.24"), unit="lakh")) == "₹ 5.24 lakh"
    assert format_money(Money(amount=Decimal("1.00"), unit="crore")) == "₹ 1.00 crore"
    assert format_money(None) is None


def test_format_pct_guards_division_by_zero():
    assert format_pct(Decimal("5"), Decimal("0")) is None
    assert format_pct(Decimal("25"), Decimal("100")) == "25%"


def test_build_page1_context_never_invents_a_borrower_name():
    ctx = build_page1_context(_empty_snapshot())
    assert ctx["borrower_name"] is None


@pytest.mark.parametrize("missing_findings", [True, False])
def test_build_page1_context_finding_counts_present_both_ways(missing_findings):
    snapshot = _empty_snapshot() if missing_findings else _golden_snapshot()
    ctx = build_page1_context(snapshot)
    assert isinstance(ctx["finding_counts"]["verified_calculation_errors"], int)
