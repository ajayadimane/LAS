"""Offline regression tests for adapted email classifier and document extraction."""

from io import BytesIO
from zipfile import ZipFile

import pymupdf
import pytest

from app.services.dpr_agentic.document_pipeline.liteparse_client import (
    LiteParsePage,
    LiteParseUnavailable,
)
from app.services.dpr_agentic.document_pipeline.pipeline import (
    DocumentProcessingError,
    inspect_document,
)


def _docx(text):
    mem = BytesIO()
    with ZipFile(mem, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f"<w:document><w:p>{text}</w:p></w:document>")
    return mem.getvalue()


def _pdf(page_count: int = 1) -> bytes:
    doc = pymupdf.open()
    for _ in range(page_count):
        doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


class FakeLiteParse:
    """Stands in for LiteParseClient -- never spawns the real CLI subprocess."""

    def __init__(self, pages=None, unavailable=False):
        self._pages = pages or []
        self._unavailable = unavailable

    def parse(self, data: bytes, *, max_pages: int) -> list[LiteParsePage]:
        if self._unavailable:
            raise LiteParseUnavailable("liteparse CLI not available in this test")
        return self._pages


def test_dpr_docx_classification():
    doc = inspect_document(
        _docx("Detailed Project Report. Means of finance"), "report.docx"
    )
    assert doc.doc_class == "DPR"
    assert not doc.needs_vision
    assert len(doc.sha256) == 64


def test_reject_unrecognized_file():
    with pytest.raises(DocumentProcessingError):
        inspect_document(b"not a document", "foo.exe")


def test_reject_oversized():
    with pytest.raises(DocumentProcessingError):
        inspect_document(_docx("abc"), "report.docx", max_bytes=1)


def test_filename_only_not_auto_approved():
    doc = inspect_document(_docx("Hello world"), "dpr.docx")
    assert doc.confidence < 0.8


def test_cost_of_the_project_phrasing_is_recognized():
    """Real-world DPRs often say 'cost of the project', not the exact
    'cost of project' substring the classifier originally required --
    confirmed against actual bank-submitted DPRs that use this phrasing."""
    doc = inspect_document(
        _docx("The cost of the project is estimated below."), "report.docx"
    )
    assert doc.doc_class == "DPR"
    assert doc.confidence >= 0.8


_DPR_TEXT = (
    "Detailed Project Report. Means of finance. Cost of project. "
    "Projected profit for year one."
)


def test_pdf_uses_liteparse_and_flags_only_the_weak_pages():
    """One weak page among several good ones -- only that page is flagged,
    the good pages' text is kept (the bug this was built to fix: today's
    whole-document fallback would discard page 1's text too)."""
    fake = FakeLiteParse(
        pages=[
            LiteParsePage(page=1, text=_DPR_TEXT, confidence=None),
            LiteParsePage(page=2, text="x", confidence=None),
        ]
    )
    doc = inspect_document(_pdf(2), "dpr.pdf", liteparse=fake)
    assert doc.low_text_pages == [2]
    assert doc.needs_vision is True
    assert doc.pages[0]["text"] == _DPR_TEXT


def test_pdf_all_pages_good_needs_no_vision_fallback():
    fake = FakeLiteParse(pages=[LiteParsePage(page=1, text=_DPR_TEXT, confidence=None)])
    doc = inspect_document(_pdf(1), "dpr.pdf", liteparse=fake)
    assert doc.low_text_pages == []
    assert doc.needs_vision is False
    assert doc.doc_class == "DPR"


def test_low_confidence_ocr_page_is_flagged_even_with_enough_characters():
    long_but_low_confidence = "a" * 60
    fake = FakeLiteParse(
        pages=[LiteParsePage(page=1, text=long_but_low_confidence, confidence=0.2)]
    )
    doc = inspect_document(_pdf(1), "dpr.pdf", liteparse=fake)
    assert doc.low_text_pages == [1]


def test_liteparse_unavailable_flags_the_whole_document_not_a_page_list():
    fake = FakeLiteParse(unavailable=True)
    doc = inspect_document(_pdf(3), "dpr.pdf", liteparse=fake)
    assert doc.low_text_pages is None
    assert doc.needs_vision is True
    assert doc.pages == []


def test_pdf_without_injected_liteparse_does_not_raise():
    """Smoke test for the real (uninjected) path: whether or not this
    environment has the LiteParse CLI/ImageMagick/Tesseract installed,
    inspect_document must degrade to a usable result, never raise."""
    doc = inspect_document(_pdf(1), "dpr.pdf")
    assert doc.mime == "application/pdf"
