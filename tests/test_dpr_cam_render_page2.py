"""Milestone 4 (part 2): Page 2 (Fix before you apply) template.

Findings/priority grouping comes from CAMSnapshot.findings as computed by
the pipeline (finding_rules.py) -- this page is a pure formatting layer,
so these tests focus on: no fabricated/invented content, correct grouping
by priority, graceful empty states, and the shared placeholder/injection/
layout guards from test_dpr_cam_render.py.
"""

import json
from pathlib import Path

from app.services.dpr_agentic.cam_adapter import build_snapshot_from_analysis
from app.services.dpr_agentic.cam_render import (
    build_page2_context,
    render_cam_report_html,
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


def test_findings_are_grouped_by_their_own_priority_field():
    snapshot = _golden_snapshot()
    ctx2 = build_page2_context(snapshot)
    must_fix_titles = {
        f.title for f in snapshot.findings if f.priority.value == "must_fix"
    }
    assert {e["title"] for e in ctx2["must_fix"]} == must_fix_titles
    should_fix_titles = {
        f.title for f in snapshot.findings if f.priority.value == "should_fix"
    }
    assert {e["title"] for e in ctx2["should_fix"]} == should_fix_titles
    good_to_fix_titles = {
        f.title for f in snapshot.findings if f.priority.value == "good_to_fix"
    }
    assert {e["title"] for e in ctx2["good_to_fix"]} == good_to_fix_titles


def test_page_reference_prefers_source_pages_over_category_fallback():
    snapshot = _golden_snapshot()
    ctx2 = build_page2_context(snapshot)
    fuel = next(e for e in ctx2["must_fix"] if "Fuel cost" in e["title"])
    assert fuel["ref"] == "p.8"


def test_page_reference_falls_back_to_category_when_no_source_page():
    snapshot = _golden_snapshot()
    ctx2 = build_page2_context(snapshot)
    dscr = next(e for e in ctx2["good_to_fix"] if "DSCR" in e["title"])
    assert dscr["ref"] == "Dscr"


def test_intro_line_never_invents_a_must_fix_count_when_there_are_none():
    snapshot = _empty_snapshot()
    ctx2 = build_page2_context(snapshot)
    assert ctx2["must_fix"] == []
    assert "No issues were found" in ctx2["intro_line"]


def test_empty_priority_groups_render_an_explicit_empty_note_not_a_blank_section():
    html = render_cam_report_html(_empty_snapshot())
    assert "No must-fix issues were found." in html
    assert "No should-fix issues were found." in html
    assert "No good-to-fix issues were found." in html


def test_page2_has_no_unresolved_jinja_placeholders():
    html = render_cam_report_html(_golden_snapshot())
    assert "{{" not in html
    assert "}}" not in html


def test_page2_has_no_canvas_runtime_markup():
    html = render_cam_report_html(_golden_snapshot())
    for marker in ("<x-dc", "<sc-if", "<sc-for", "support.js", "data-props"):
        assert marker not in html


def test_page2_llm_authored_finding_text_cannot_inject_markup():
    snapshot = _golden_snapshot()
    injected = snapshot.findings[0].model_copy(
        update={"title": "<script>alert(1)</script>"}
    )
    snapshot = snapshot.model_copy(
        update={"findings": [injected, *snapshot.findings[1:]]}
    )
    html = render_cam_report_html(snapshot)
    assert "<script>alert(1)</script>" not in html


def test_page2_shows_page_2_of_6():
    html = render_cam_report_html(_golden_snapshot())
    assert "Page 2 of 6" in html
