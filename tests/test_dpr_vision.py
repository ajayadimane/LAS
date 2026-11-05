"""Tests for app/services/dpr_agentic/document_pipeline/vision.py."""

import asyncio

import pymupdf
import pytest

from app.services.dpr_agentic.document_pipeline.sniff import DOCX, JPEG, PDF, PNG
from app.services.dpr_agentic.document_pipeline.vision import (
    extract_pages_via_vision,
    render_pages_to_images,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _blank_pdf(page_count: int) -> bytes:
    doc = pymupdf.open()
    for _ in range(page_count):
        doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def test_renders_one_png_per_pdf_page():
    images = render_pages_to_images(_blank_pdf(3), PDF, max_pages=20)
    assert len(images) == 3
    assert all(img.startswith(PNG_MAGIC) for img in images)


def test_caps_rendering_at_max_pages():
    images = render_pages_to_images(_blank_pdf(5), PDF, max_pages=2)
    assert len(images) == 2


def test_an_image_mime_is_returned_as_its_own_single_page():
    data = b"\xff\xd8\xff fake jpeg bytes"
    assert render_pages_to_images(data, JPEG, max_pages=20) == [data]
    png_data = PNG_MAGIC + b"fake png bytes"
    assert render_pages_to_images(png_data, PNG, max_pages=20) == [png_data]


def test_an_unsupported_mime_renders_nothing():
    assert render_pages_to_images(b"whatever", DOCX, max_pages=20) == []


def test_renders_only_the_requested_pages_in_requested_order():
    images = render_pages_to_images(_blank_pdf(5), PDF, max_pages=20, pages=[3, 1])
    assert len(images) == 2
    assert all(img.startswith(PNG_MAGIC) for img in images)


def test_out_of_range_requested_pages_are_skipped():
    images = render_pages_to_images(_blank_pdf(3), PDF, max_pages=20, pages=[1, 99])
    assert len(images) == 1


def test_requested_pages_ignore_max_pages_cap():
    """pages= targets a specific fallback set (e.g. page 10 of a 10-page doc)
    -- it must not be truncated by max_pages, which only bounds the
    whole-document (pages=None) fallback."""
    images = render_pages_to_images(_blank_pdf(10), PDF, max_pages=2, pages=[10])
    assert len(images) == 1


class FakeGemini:
    def __init__(self):
        self.calls: list[bytes] = []

    async def ocr_page(self, image_bytes: bytes, mime_type: str = "image/png") -> str:
        self.calls.append(image_bytes)
        return f"page text {len(self.calls)}"


def test_extract_pages_via_vision_calls_ocr_once_per_image_in_order():
    images = [b"img1", b"img2", b"img3"]
    gemini = FakeGemini()

    pages = asyncio.run(extract_pages_via_vision(gemini, images))

    assert gemini.calls == images
    assert pages == [
        {"page": 1, "text": "page text 1"},
        {"page": 2, "text": "page text 2"},
        {"page": 3, "text": "page text 3"},
    ]


def test_extract_pages_via_vision_with_no_images_is_a_safe_no_op():
    assert asyncio.run(extract_pages_via_vision(FakeGemini(), [])) == []


def test_one_failed_page_fails_the_whole_extraction():
    """Partial text would mean analyzing an incomplete document silently --
    the caller must get a clean failure instead, not a gap."""

    class FlakyGemini(FakeGemini):
        async def ocr_page(self, image_bytes, mime_type="image/png"):
            if image_bytes == b"bad":
                raise RuntimeError("vision call failed")
            return await super().ocr_page(image_bytes, mime_type)

    with pytest.raises(RuntimeError):
        asyncio.run(extract_pages_via_vision(FlakyGemini(), [b"good", b"bad"]))
