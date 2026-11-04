"""Tests for app/services/dpr_agentic/report_render.py.

Verifies actual rendered content by extracting text back out with pymupdf --
already a project dependency -- rather than just checking the PDF opens.
"""

import pymupdf

from app.services.dpr_agentic.report_render import render_report
from app.services.dpr_agentic.schemas import AgentOutput, AnalysisResult, Finding

SHA = "a" * 64


def _text(pdf_bytes: bytes) -> str:
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


def _result(**overrides) -> AnalysisResult:
    defaults = dict(
        service_request_id="9000000013:GEO_CHECK",
        wallet_document_id="wallet-person-custom-field:9000000013:GEO_CHECK",
        document_sha256=SHA,
        status="completed",
        agent_outputs={"document": AgentOutput(summary="ok")},
        findings=[],
        unresolved_questions=[],
    )
    defaults.update(overrides)
    return AnalysisResult(**defaults)


def test_produces_a_real_pdf():
    pdf_bytes = render_report(_result())
    assert pdf_bytes[:5] == b"%PDF-"


def test_includes_the_disclaimer_and_identifying_metadata():
    result = _result()
    raw = _text(render_report(result))
    # Word-wrap only replaces a space with a newline, so rejoining with a
    # single space recovers normal text; the SHA has no internal spaces to
    # begin with, so it needs every whitespace character removed instead.
    spaced = " ".join(raw.split())
    unspaced = "".join(raw.split())
    assert result.disclaimer in spaced
    assert result.service_request_id in spaced
    assert SHA in unspaced


def test_a_calculated_finding_appears_under_calculated_figures_as_code_verified():
    calculated = Finding(
        category="financial_calculation",
        severity="high",
        title="Cost Of Project Total does not reconcile",
        explanation="Computed 100.00; the document states 500.00 (difference -400.00).",
        suggested_action="Verify these figures with the applicant.",
        provenance="calculated",
        verification="source_verified",
    )
    text = _text(render_report(_result(findings=[calculated])))
    assert "Calculated Figures" in text
    assert calculated.title in text
    assert "code-verified" in text


def test_evidence_page_zero_still_renders_a_page_annotation():
    """Page 0 should never occur from a real document (SourcePage.page
    requires >= 1), but the renderer must not treat it as "no page" via
    Python's falsy-zero -- that exact bug (`if f.evidence_page else ""`)
    was found and fixed in cam_adapter.py (milestone 3) and here
    (milestone 2b); this guards the fix."""
    finding = Finding(
        category="inconsistency",
        severity="medium",
        title="x",
        explanation="x",
        evidence_page=0,
        evidence_quote="some quote",
        suggested_action="x",
    )
    text = _text(render_report(_result(findings=[finding])))
    assert "page 0" in text


def test_an_ai_interpreted_finding_appears_under_findings_not_calculated_figures():
    interpreted = Finding(
        category="funding",
        severity="medium",
        title="Funding components need verification",
        explanation="Additional funding details may be required",
        evidence_page=3,
        evidence_quote="Project cost is 80 lakh",
        suggested_action="Provide complete means of finance",
    )
    text = _text(render_report(_result(findings=[interpreted])))
    calc_idx = text.index("Calculated Figures")
    findings_idx = text.index("Findings")
    title_idx = text.index(interpreted.title)
    assert findings_idx > calc_idx
    assert title_idx > findings_idx
    assert "AI interpretation" in text
    assert "Project cost is 80 lakh" in text
    assert "page 3" in text


def test_interpreted_findings_are_ordered_most_severe_first():
    low = Finding(
        category="x",
        severity="low",
        title="Low severity item",
        explanation="e",
        suggested_action="a",
    )
    critical = Finding(
        category="x",
        severity="critical",
        title="Critical severity item",
        explanation="e",
        suggested_action="a",
    )
    text = _text(render_report(_result(findings=[low, critical])))
    assert text.index("Critical severity item") < text.index("Low severity item")


def test_unresolved_questions_are_listed():
    text = _text(
        render_report(_result(unresolved_questions=["Is the land already owned?"]))
    )
    assert "Is the land already owned?" in text


def test_renders_cleanly_with_no_findings_or_questions():
    text = _text(render_report(_result()))
    assert "No figures could be independently recomputed" in text
    assert "No additional findings." in text
    assert "None." in text
